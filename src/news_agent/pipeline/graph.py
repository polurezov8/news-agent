"""LangGraph pipeline: ingest → dedup → tag → filter → score → route → notify."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypedDict

from langgraph.graph import END, StateGraph

from news_agent.config.schema import (
    PriorsConfig,
    SourcesConfig,
    TagsConfig,
    TopicsConfig,
)
from news_agent.core.types import (
    Article,
    ArticleId,
    Cadence,
    PipelineCounters,
    ScoreResult,
    SourceId,
    SurfaceRef,
    Tag,
    TopicId,
)
from news_agent.notifier.base import DigestPayload, PriorityPayload
from news_agent.pipeline.scoring import (
    compute_final,
    decay_factor,
    matches_topic,
    tag_adjustment,
    tag_to_category,
)
from news_agent.sources import make_source
from news_agent.storage.repository import (
    article_exists_by_hash,
    connect,
    has_surface,
    load_priors_dict,
    monthly_usd_cents,
    save_score,
    save_surface,
    save_tag_result,
    upsert_article,
)


class PipelineState(TypedDict):
    raw_articles: list[Article]
    new_articles: list[Article]
    tag_map: dict[str, frozenset[Tag]]
    matches: list[tuple[str, str]]                  # (article_id, topic_id)
    score_results: list[ScoreResult]
    digest_items: list[tuple[Article, ScoreResult]]
    priority_items: list[tuple[Article, ScoreResult]]
    surface_refs: list[SurfaceRef]
    counters: PipelineCounters


@dataclass
class PipelineDeps:
    sources_cfg: SourcesConfig
    topics_cfg: TopicsConfig
    tags_cfg: TagsConfig
    priors_cfg: PriorsConfig
    db_path: Path
    notifier: Any                                   # SlackNotifier or None
    tagger_model: str = "claude-haiku-4-5-20251001"
    scorer_model: str = "claude-sonnet-4-6"
    cadence: Cadence = Cadence.DAILY                # DAILY | PRIORITY (WEEKLY runs outside the graph)
    budget_usd: float = 5.0                         # monthly cap; pipeline bails if exceeded
    log: Callable[[str], None] = print


def empty_state() -> PipelineState:
    return PipelineState(
        raw_articles=[],
        new_articles=[],
        tag_map={},
        matches=[],
        score_results=[],
        digest_items=[],
        priority_items=[],
        surface_refs=[],
        counters=PipelineCounters(),
    )


def _bump(counters: PipelineCounters, **deltas: int) -> PipelineCounters:
    """Return a new PipelineCounters with the given fields replaced."""
    from dataclasses import replace
    return replace(counters, **deltas)


def build_graph(deps: PipelineDeps):
    """Return a compiled LangGraph pipeline bound to deps."""

    cat_lookup = tag_to_category(deps.tags_cfg)
    source_topics: dict[str, list[str]] = {
        s.id: s.topics for s in deps.sources_cfg.sources
    }
    source_weight_default: dict[str, float] = {
        s.id: s.weight for s in deps.sources_cfg.sources
    }
    # Priors: start with YAML seeds, overlay DB (DB wins; reflects learning loop updates).
    prior_lookup: dict[tuple[str, str], float] = {
        (p.source, p.topic): p.weight for p in deps.priors_cfg.priors
    }
    _conn = connect(deps.db_path)
    try:
        prior_lookup.update(load_priors_dict(_conn))
    finally:
        _conn.close()

    def _source_weight(source: SourceId, topic: TopicId) -> float:
        sw = prior_lookup.get((str(source), str(topic)))
        if sw is not None:
            return sw
        return source_weight_default.get(str(source), 1.0)

    # ---- nodes ----

    def ingest(state: PipelineState) -> dict:
        # Budget gate runs in the entry node so even ingest can short-circuit.
        budget_cents = int(round(deps.budget_usd * 100))
        conn = connect(deps.db_path)
        try:
            spent = monthly_usd_cents(conn, now=datetime.now(timezone.utc))
        finally:
            conn.close()
        if spent >= budget_cents:
            deps.log(
                f"BUDGET HIT: spent=${spent/100:.2f} cap=${deps.budget_usd:.2f} — aborting run"
            )
            return {"raw_articles": [], "counters": _bump(state["counters"], fetched=0)}

        out: list[Article] = []
        for sc in deps.sources_cfg.sources:
            if not sc.enabled:
                continue
            try:
                src = make_source(sc)
                fetched = src.fetch()
                deps.log(f"ingest: {sc.id} → {len(fetched)} items")
                out.extend(fetched)
            except Exception as exc:
                deps.log(f"ingest: {sc.id} FAILED — {exc}")
        return {"raw_articles": out, "counters": _bump(state["counters"], fetched=len(out))}

    def dedup(state: PipelineState) -> dict:
        """In-memory filter only. Articles are persisted by tag_node once tagging succeeds."""
        conn = connect(deps.db_path)
        new: list[Article] = []
        try:
            for a in state["raw_articles"]:
                if not article_exists_by_hash(conn, a.content_hash):
                    new.append(a)
        finally:
            conn.close()
        deps.log(f"dedup: {len(new)} new of {len(state['raw_articles'])} fetched")
        return {"new_articles": new, "counters": _bump(state["counters"], new=len(new))}

    def tag_node(state: PipelineState) -> dict:
        if not state["new_articles"]:
            return {"tag_map": {}, "counters": _bump(state["counters"], tagged=0)}

        from news_agent.llm._counting_client import CountingClient
        from news_agent.llm.tagger import _client as _tagger_client_factory
        from news_agent.llm.tagger import tag_articles

        tagger_client = CountingClient(
            _tagger_client_factory(deps.tagger_model),
            model=deps.tagger_model, purpose="tag", db_path=deps.db_path,
        )
        results = tag_articles(
            state["new_articles"], deps.tags_cfg,
            model=deps.tagger_model, client=tagger_client,
        )
        articles_by_id = {str(a.id): a for a in state["new_articles"]}
        now = datetime.now(timezone.utc)
        conn = connect(deps.db_path)
        try:
            for r in results:
                article = articles_by_id.get(str(r.article))
                if article is None:
                    continue
                upsert_article(conn, article)
                save_tag_result(conn, r, now, category_lookup=cat_lookup)
            conn.commit()
        finally:
            conn.close()
        deps.log(f"tag: {len(results)} articles tagged + persisted")
        return {
            "tag_map": {str(r.article): r.tags for r in results},
            "counters": _bump(state["counters"], tagged=len(results)),
        }

    def filter_topics(state: PipelineState) -> dict:
        article_by_id = {str(a.id): a for a in state["new_articles"]}
        matches: list[tuple[str, str]] = []
        for article_id, tags in state["tag_map"].items():
            article = article_by_id.get(article_id)
            if article is None:
                continue
            for topic_id in source_topics.get(str(article.source), []):
                topic_cfg = deps.topics_cfg.topics.get(topic_id)
                if topic_cfg and matches_topic(tags, topic_cfg):
                    matches.append((article_id, topic_id))
        deps.log(f"filter: {len(matches)} (article, topic) pairs matched")
        on_topic = len({aid for aid, _ in matches})
        return {"matches": matches, "counters": _bump(state["counters"], on_topic=on_topic)}

    def score_node(state: PipelineState) -> dict:
        if not state["matches"]:
            return {"score_results": [], "counters": _bump(state["counters"], scored=0)}

        from news_agent.llm._counting_client import CountingClient
        from news_agent.llm.scorer import _client as _scorer_client_factory
        from news_agent.llm.scorer import score_batch_for_topic

        scorer_client = CountingClient(
            _scorer_client_factory(deps.scorer_model),
            model=deps.scorer_model, purpose="score", db_path=deps.db_path,
        )
        article_by_id = {str(a.id): a for a in state["new_articles"]}

        # Group matches by topic so we can batch a single LLM call per topic.
        by_topic: dict[str, list[tuple[str, Article, frozenset[Tag]]]] = {}
        for article_id, topic_id in state["matches"]:
            article = article_by_id.get(article_id)
            if article is None:
                continue
            tags = state["tag_map"].get(article_id, frozenset())
            by_topic.setdefault(topic_id, []).append((article_id, article, tags))

        results: list[ScoreResult] = []
        now = datetime.now(timezone.utc)
        for topic_id, items in by_topic.items():
            topic_cfg = deps.topics_cfg.topics.get(topic_id)
            if topic_cfg is None:
                continue
            substances = score_batch_for_topic(
                [(article, tags) for _aid, article, tags in items],
                topic_cfg,
                model=deps.scorer_model,
                client=scorer_client,
            )
            # Open the connection only after the LLM call returns, so we don't
            # hold a writer while CountingClient writes its cost row.
            conn = connect(deps.db_path)
            try:
                for (article_id, article, tags), substance in zip(items, substances):
                    adj = tag_adjustment(tags, topic_cfg)
                    d = decay_factor(article.published_at, topic_cfg.recency.half_life_days)
                    sw = _source_weight(article.source, TopicId(topic_id))
                    final = compute_final(substance, adj, d, sw)
                    sr = ScoreResult(
                        article=ArticleId(article_id),
                        topic=TopicId(topic_id),
                        substance=substance,
                        tag_adj=adj,
                        decay=d,
                        source_weight=sw,
                        final=final,
                    )
                    save_score(conn, sr, now)
                    results.append(sr)
                conn.commit()
            finally:
                conn.close()
        deps.log(f"score: {len(results)} pairs scored ({len(by_topic)} batches)")
        return {"score_results": results, "counters": _bump(state["counters"], scored=len(results))}

    def route(state: PipelineState) -> dict:
        article_by_id = {str(a.id): a for a in state["new_articles"]}
        now = datetime.now(timezone.utc)
        digest: list[tuple[Article, ScoreResult]] = []
        priority: list[tuple[Article, ScoreResult]] = []

        for sr in state["score_results"]:
            article = article_by_id.get(str(sr.article))
            topic_cfg = deps.topics_cfg.topics.get(str(sr.topic))
            if article is None or topic_cfg is None:
                continue
            age_h = (now - article.published_at).total_seconds() / 3600
            d = topic_cfg.delivery
            if sr.final >= d.priority_threshold and age_h <= d.priority_recency_hours:
                priority.append((article, sr))
            else:
                digest.append((article, sr))

        # Dedup priority against previously-DM'd articles (relevant for hourly priority runs).
        conn = connect(deps.db_path)
        try:
            priority = [
                item for item in priority
                if not has_surface(conn, item[1].article, item[1].topic, Cadence.PRIORITY)
            ]
        finally:
            conn.close()

        if deps.cadence is Cadence.PRIORITY:
            deps.log(f"route[priority]: {len(priority)} new priority items (digest suppressed)")
            return {
                "digest_items": [],
                "priority_items": priority,
                "counters": _bump(state["counters"], surfaced=len(priority)),
            }

        # Daily digest: pure quality routing, hard global cap of 3 items.
        # Each (article, topic) pair must clear topic.delivery.digest_min_score;
        # the top finals (by score) fill up to the cap. If nothing clears, the
        # digest is empty and notify stays silent — quality over noise.
        DIGEST_CAP = 3
        quality_candidates: list[tuple[Article, ScoreResult]] = []
        for item in digest:
            tid = str(item[1].topic)
            topic_cfg = deps.topics_cfg.topics.get(tid)
            if topic_cfg is None:
                continue
            if item[1].final < topic_cfg.delivery.digest_min_score:
                continue
            quality_candidates.append(item)
        quality_candidates.sort(key=lambda x: x[1].final, reverse=True)
        chosen = quality_candidates[:DIGEST_CAP]

        deps.log(f"route[daily]: digest={len(chosen)} priority={len(priority)}")
        return {
            "digest_items": chosen,
            "priority_items": priority,
            "counters": _bump(state["counters"], surfaced=len(chosen) + len(priority)),
        }

    def notify(state: PipelineState) -> dict:
        if deps.notifier is None:
            deps.log("notify: skipped (dry-run)")
            return {"surface_refs": []}

        refs: list[SurfaceRef] = []
        conn = connect(deps.db_path)
        try:
            if state["digest_items"]:
                topic_order = [TopicId(t) for t in deps.topics_cfg.topics]
                topic_labels = {
                    TopicId(tid): (tcfg.emoji, tcfg.label)
                    for tid, tcfg in deps.topics_cfg.topics.items()
                }
                ref = deps.notifier.post_digest(
                    DigestPayload(
                        items=state["digest_items"],
                        topic_order=topic_order,
                        topic_labels=topic_labels,
                        counters=state["counters"],
                    )
                )
                refs.append(ref)
                for article, sr in state["digest_items"]:
                    save_surface(conn, sr.article, sr.topic, ref, Cadence.DAILY)

            priority_topic_labels = {
                TopicId(tid): (tcfg.emoji, tcfg.label)
                for tid, tcfg in deps.topics_cfg.topics.items()
            }
            for article, sr in state["priority_items"]:
                ref = deps.notifier.dm_priority(
                    PriorityPayload(
                        article=article,
                        score=sr,
                        topic_labels=priority_topic_labels,
                    )
                )
                refs.append(ref)
                save_surface(conn, sr.article, sr.topic, ref, Cadence.PRIORITY)
            conn.commit()
        finally:
            conn.close()
        deps.log(f"notify: posted {len(refs)} messages")
        return {"surface_refs": refs}

    # ---- wiring ----

    g = StateGraph(PipelineState)
    g.add_node("ingest", ingest)
    g.add_node("dedup", dedup)
    g.add_node("tag", tag_node)
    g.add_node("filter", filter_topics)
    g.add_node("score", score_node)
    g.add_node("route", route)
    g.add_node("notify", notify)

    g.set_entry_point("ingest")
    g.add_edge("ingest", "dedup")
    g.add_edge("dedup", "tag")
    g.add_edge("tag", "filter")
    g.add_edge("filter", "score")
    g.add_edge("score", "route")
    g.add_edge("route", "notify")
    g.add_edge("notify", END)

    return g.compile()
