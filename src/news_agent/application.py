"""Application use cases — one named function per agent action.

This is the seam between frontends (CLI, Slack listener) and the domain.
Both frontends bootstrap the same way (load configs, init DB, construct
PipelineDeps); centralising that here removes duplication and gives the
operations a single place to evolve.

Frontends keep: arg parsing, Rich/Slack rendering, exit-code mapping.
This module owns: config loading, DB initialisation, deps construction,
graph invocation, correction persistence + prior update.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .config.loader import load_priors, load_sources, load_tags, load_topics
from .config.schema import PriorsConfig, SourcesConfig, TagsConfig, TopicsConfig
from .core.types import (
    Article,
    Cadence,
    CorrectionEvent,
    CorrectionKind,
    ScoreResult,
    SourceId,
    TopicId,
)
from .learning.priors import updated_prior
from .pipeline.graph import PipelineDeps, build_graph, empty_state
from .pipeline.transactor import NullTransactor, SqliteTransactor, Transactor
from .storage.repository import (
    connect,
    get_source_prior,
    init_db,
    save_audit,
    save_correction,
    upsert_source_prior,
)


class ApplicationError(Exception):
    """Raised when application invariants fail. Frontends translate to UI."""


@dataclass(frozen=True)
class AppContext:
    """Loaded configuration + paths + model knobs. Built once per run."""

    sources_cfg: SourcesConfig
    topics_cfg: TopicsConfig
    tags_cfg: TagsConfig
    priors_cfg: PriorsConfig
    db_path: Path
    tagger_model: str
    scorer_model: str
    budget_usd: float


@dataclass(frozen=True)
class PipelineResult:
    """Counters + items the pipeline produced. Frontends format these."""

    cadence: Cadence
    fetched: int
    new: int
    scored: int
    digest_items: list[tuple[Article, ScoreResult]]
    priority_items: list[tuple[Article, ScoreResult]]
    posted: int
    dry_run: bool


@dataclass(frozen=True)
class CorrectionOutcome:
    """Result of applying a Slack reaction/button correction to learning state."""

    event: CorrectionEvent
    prior_before: float
    prior_after: float


# ---------------------------------------------------------------- #
# Context construction
# ---------------------------------------------------------------- #


def load_default_context() -> AppContext:
    """Read configs from disk + env, init the DB, return a frozen AppContext.

    Raises ApplicationError if a required config is empty or env var missing
    for callers that need them (the caller in turn picks the right exit code).
    """
    db_path = Path(os.environ.get("NEWS_AGENT_DB", "./news_agent.db"))
    init_db(db_path)
    return AppContext(
        sources_cfg=load_sources(),
        topics_cfg=load_topics(),
        tags_cfg=load_tags(),
        priors_cfg=load_priors(),
        db_path=db_path,
        tagger_model=os.environ.get("NEWS_AGENT_MODEL_FAST", "claude-haiku-4-5-20251001"),
        scorer_model=os.environ.get("NEWS_AGENT_MODEL_SMART", "claude-sonnet-4-6"),
        budget_usd=float(os.environ.get("NEWS_AGENT_BUDGET_USD", "5")),
    )


# ---------------------------------------------------------------- #
# Use cases
# ---------------------------------------------------------------- #


def run_pipeline(
    cadence: Cadence,
    *,
    ctx: AppContext | None = None,
    dry_run: bool = False,
    no_slack: bool = False,
    log: Callable[[str], None] = print,
) -> PipelineResult:
    """Run a daily / priority pipeline pass and return a typed result.

    Pre-conditions enforced here (so frontends don't repeat them):
      - sources.yaml not empty
      - topics.yaml not empty
      - ANTHROPIC_API_KEY set in env
    """
    if cadence is Cadence.ON_DEMAND:
        raise ApplicationError("on_demand cadence is for /news, not run_pipeline")
    if cadence is Cadence.WEEKLY:
        raise ApplicationError("use run_weekly() for weekly recap")

    ctx = ctx or load_default_context()
    _require_pipeline_config(ctx)

    notifier = _build_notifier(no_slack=no_slack, dry_run=dry_run)
    transactor = _build_transactor(ctx.db_path, dry_run=dry_run, log=log)
    deps = PipelineDeps(
        sources_cfg=ctx.sources_cfg,
        topics_cfg=ctx.topics_cfg,
        tags_cfg=ctx.tags_cfg,
        priors_cfg=ctx.priors_cfg,
        db_path=ctx.db_path,
        notifier=notifier,
        transactor=transactor,
        tagger_model=ctx.tagger_model,
        scorer_model=ctx.scorer_model,
        cadence=cadence,
        budget_usd=ctx.budget_usd,
        log=log,
    )
    # Refresh the taste profile from the reading list before scoring, so today's
    # picks already reflect what you've been reading. Daily only — the hourly
    # priority pass shouldn't re-tag the reading list. Skipped on dry-run.
    if cadence is Cadence.DAILY and not dry_run:
        from .pipeline.interest import sync_interest

        sync_interest(deps)
    state = build_graph(deps).invoke(empty_state())

    return PipelineResult(
        cadence=cadence,
        fetched=len(state["raw_articles"]),
        new=len(state["new_articles"]),
        scored=len(state["score_results"]),
        digest_items=list(state["digest_items"]),
        priority_items=list(state["priority_items"]),
        posted=len(state["surface_refs"]),
        dry_run=dry_run,
    )


def run_weekly(
    *,
    ctx: AppContext | None = None,
    no_slack: bool = False,
    log: Callable[[str], None] = print,
) -> int:
    """Build and post the weekly recap. Returns the number of items surfaced.

    Dry-run is intentionally not supported here — the recap reads from the
    `surfaces` table and produces a single aggregate post; preview without
    state mutation is not meaningful.
    """
    from .pipeline.recap import run_weekly_recap

    ctx = ctx or load_default_context()
    notifier = _build_notifier(no_slack=no_slack, dry_run=False)
    transactor = _build_transactor(ctx.db_path, dry_run=False, log=log)
    deps = PipelineDeps(
        sources_cfg=ctx.sources_cfg,
        topics_cfg=ctx.topics_cfg,
        tags_cfg=ctx.tags_cfg,
        priors_cfg=ctx.priors_cfg,
        db_path=ctx.db_path,
        notifier=notifier,
        transactor=transactor,
        tagger_model=ctx.tagger_model,
        scorer_model=ctx.scorer_model,
        cadence=Cadence.WEEKLY,
        budget_usd=ctx.budget_usd,
        log=log,
    )
    return run_weekly_recap(deps)


def handle_correction(
    event: CorrectionEvent,
    *,
    db_path: Path | None = None,
    log: Callable[[str], None] = print,
) -> CorrectionOutcome:
    """Persist a correction event and update the (source, topic) prior.

    Used by the Slack reaction/button handler. Returns the before/after
    priors so the listener can surface a learning trace.
    """
    db_path = db_path or Path(os.environ.get("NEWS_AGENT_DB", "./news_agent.db"))
    conn = connect(db_path)
    try:
        save_correction(conn, event)
        current = get_source_prior(conn, event.source, event.topic)
        base = 0.5 if current is None else current
        new = updated_prior(base, event.kind)
        upsert_source_prior(conn, event.source, event.topic, new, datetime.now(timezone.utc))
        conn.commit()
    finally:
        conn.close()

    log(f"learn: {event.kind.value} {event.source}/{event.topic} prior {base:.2f}→{new:.2f}")
    return CorrectionOutcome(event=event, prior_before=base, prior_after=new)


def adjust_source_prior(
    source: str,
    direction: str,
    *,
    topic: str | None = None,
    ctx: AppContext | None = None,
    db_path: Path | None = None,
    log: Callable[[str], None] = print,
) -> list[tuple[str, float, float]]:
    """Nudge a source's (source, topic) prior up or down from a chat request.

    direction is "boost" or "demote". When topic is None the nudge applies to
    every topic the source declares in sources.yaml, so "boost pointfree" Just
    Works. Returns [(topic, before, after), ...]. Changes are reversible and
    logged to audit_log.
    """
    if direction not in ("boost", "demote"):
        raise ApplicationError(f"direction must be boost|demote, got {direction!r}")
    ctx = ctx or load_default_context()
    db_path = db_path or ctx.db_path

    declared = {s.id: s.topics for s in ctx.sources_cfg.sources}
    if source not in declared:
        raise ApplicationError(f"Unknown source {source!r}.")
    topics = [topic] if topic else declared[source]
    if not topics:
        raise ApplicationError(f"Source {source!r} declares no topics.")

    kind = CorrectionKind.BOOST if direction == "boost" else CorrectionKind.DEMOTE
    now = datetime.now(timezone.utc)
    changes: list[tuple[str, float, float]] = []
    conn = connect(db_path)
    try:
        for t in topics:
            current = get_source_prior(conn, SourceId(source), TopicId(t))
            base = 0.5 if current is None else current
            new = updated_prior(base, kind)
            upsert_source_prior(conn, SourceId(source), TopicId(t), new, now)
            save_audit(
                conn,
                event=f"chat_tune:{direction}",
                article_id=None,
                payload_json=f'{{"source":"{source}","topic":"{t}","from":{base:.3f},"to":{new:.3f}}}',
                at=now,
            )
            changes.append((t, base, new))
        conn.commit()
    finally:
        conn.close()

    log(f"tune: {direction} {source} → " + ", ".join(f"{t} {b:.2f}→{a:.2f}" for t, b, a in changes))
    return changes


# ---------------------------------------------------------------- #
# Internal helpers
# ---------------------------------------------------------------- #


def _require_pipeline_config(ctx: AppContext) -> None:
    if not ctx.sources_cfg.sources:
        raise ApplicationError("No sources configured. Edit config/sources.yaml.")
    if not ctx.topics_cfg.topics:
        raise ApplicationError("No topics configured. Edit config/topics.yaml.")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ApplicationError("Missing ANTHROPIC_API_KEY.")


def _build_notifier(*, no_slack: bool, dry_run: bool):
    """Return a SlackNotifier if env is set and posts are requested, else None.

    None means "skip Slack" — the pipeline's notify node already handles that.
    """
    if no_slack or dry_run:
        return None
    missing = [v for v in ("SLACK_BOT_TOKEN", "SLACK_USER_ID") if not os.environ.get(v)]
    if missing:
        raise ApplicationError(
            f"Missing env vars: {', '.join(missing)}. Use no_slack=True to skip Slack delivery."
        )
    from .notifier.slack import SlackNotifier

    return SlackNotifier.from_env()


def _build_transactor(
    db_path: Path,
    *,
    dry_run: bool,
    log: Callable[[str], None],
) -> Transactor:
    """NullTransactor when dry-run; SqliteTransactor otherwise."""
    return NullTransactor(log=log) if dry_run else SqliteTransactor(db_path)
