"""Weekly recap: query DB for the last N days; no fetch, no LLM."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol

from news_agent.core.types import Cadence, CorrectionKind, SurfaceRef, TopicId
from news_agent.learning.taste import top_taste, uncovered_interest_tags
from news_agent.notifier.base import RecapPayload, WeeklyStats
from news_agent.pipeline.graph import PipelineDeps
from news_agent.pipeline.scoring import tag_to_category
from news_agent.storage.repository import (
    connect,
    count_corrections_by_kind,
    count_distinct_run_days,
    count_surfaces_in_window,
    load_taste,
    query_skipped_high,
    query_top_scored,
    save_surface,
    top_sources_in_window,
)


class _RecapNotifier(Protocol):
    def post_recap(self, payload: RecapPayload) -> SurfaceRef: ...


def run_weekly_recap(deps: PipelineDeps, *, window_days: int = 7) -> int:
    """Build and post a weekly recap. Returns total items surfaced."""
    if deps.notifier is None:
        deps.log("recap: skipped (no notifier configured)")
        return 0

    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    conn = connect(deps.db_path)
    try:
        top = query_top_scored(conn, since=since, limit=10)
        skipped = query_skipped_high(conn, since=since, min_score=0.85, limit=20)
        stats = WeeklyStats(
            runs=count_distinct_run_days(conn, since=since, cadence=Cadence.DAILY),
            surfaced=count_surfaces_in_window(conn, since=since, cadence=Cadence.DAILY),
            boosted=count_corrections_by_kind(conn, since=since, kind=CorrectionKind.BOOST),
            demoted=count_corrections_by_kind(conn, since=since, kind=CorrectionKind.DEMOTE),
            top_sources=top_sources_in_window(
                conn, since=since, cadence=Cadence.DAILY, limit=3,
            ),
        )
        taste = load_taste(conn)
    finally:
        conn.close()

    taste_top = top_taste(taste, n=8)
    covered = {
        t for tcfg in deps.topics_cfg.topics.values() for t in tcfg.query.must_have_any
    }
    uncovered = uncovered_interest_tags(taste, covered, tag_to_category(deps.tags_cfg))

    deps.log(f"recap: top={len(top)} skipped={len(skipped)} window={window_days}d")

    if not top and not skipped and not taste_top:
        deps.log("recap: nothing to post")
        return 0

    topic_labels = {
        TopicId(tid): (tcfg.emoji, tcfg.label)
        for tid, tcfg in deps.topics_cfg.topics.items()
    }
    payload = RecapPayload(
        top_items=top,
        skipped_but_high=skipped,
        window_days=window_days,
        topic_labels=topic_labels,
        stats=stats,
        taste_top=taste_top,
        uncovered_tags=uncovered,
    )
    ref: SurfaceRef = deps.notifier.post_recap(payload)

    conn = connect(deps.db_path)
    try:
        for _article, sr in top + skipped:
            save_surface(conn, sr.article, sr.topic, ref, Cadence.WEEKLY)
        conn.commit()
    finally:
        conn.close()

    return len(top) + len(skipped)
