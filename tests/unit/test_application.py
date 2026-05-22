"""Tests for the news-agent application use case seam."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from news_agent import application
from news_agent.application import (
    ApplicationError,
    AppContext,
    CorrectionOutcome,
    PipelineResult,
)
from news_agent.config.schema import (
    PriorEntry,
    PriorsConfig,
    SourceConfig,
    SourcesConfig,
    TagsConfig,
    TopicConfig,
    TopicDelivery,
    TopicQuery,
    TopicRecency,
    TopicsConfig,
)
from news_agent.core.types import (
    ArticleId,
    Cadence,
    CorrectionEvent,
    CorrectionKind,
    SourceId,
    SurfaceRef,
    TopicId,
)


def _ctx(tmp_path: Path, *, with_source: bool = True, with_topic: bool = True) -> AppContext:
    return AppContext(
        sources_cfg=SourcesConfig(sources=[
            SourceConfig(id="fakesrc", type="_fake_test", config={}, topics=["topic_a"], weight=1.0),
        ] if with_source else []),
        topics_cfg=TopicsConfig(topics={
            "topic_a": TopicConfig(
                label="AI",
                emoji="🤖",
                query=TopicQuery(must_have_any=["ai"]),
                recency=TopicRecency(half_life_days=7),
                delivery=TopicDelivery(priority_threshold=0.95, priority_recency_hours=24, digest_top_n=3),
                nl_rules=[],
            ),
        } if with_topic else {}),
        tags_cfg=TagsConfig(tags={"domain": ["ai"]}),
        priors_cfg=PriorsConfig(priors=[
            PriorEntry(source="fakesrc", topic="topic_a", weight=1.0),
        ]),
        db_path=tmp_path / "t.db",
        tagger_model="haiku-test",
        scorer_model="sonnet-test",
        budget_usd=5.0,
    )


# ---------------------------------------------------------------- #
# Pre-condition validation
# ---------------------------------------------------------------- #


def test_run_pipeline_rejects_weekly_cadence(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    ctx = _ctx(tmp_path)
    with pytest.raises(ApplicationError, match="weekly"):
        application.run_pipeline(Cadence.WEEKLY, ctx=ctx)


def test_run_pipeline_rejects_on_demand_cadence(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    ctx = _ctx(tmp_path)
    with pytest.raises(ApplicationError, match="on_demand"):
        application.run_pipeline(Cadence.ON_DEMAND, ctx=ctx)


def test_run_pipeline_requires_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    ctx = _ctx(tmp_path, with_source=False)
    with pytest.raises(ApplicationError, match="sources"):
        application.run_pipeline(Cadence.DAILY, ctx=ctx)


def test_run_pipeline_requires_topics(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    ctx = _ctx(tmp_path, with_topic=False)
    with pytest.raises(ApplicationError, match="topics"):
        application.run_pipeline(Cadence.DAILY, ctx=ctx)


def test_run_pipeline_requires_anthropic_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ctx = _ctx(tmp_path)
    with pytest.raises(ApplicationError, match="ANTHROPIC_API_KEY"):
        application.run_pipeline(Cadence.DAILY, ctx=ctx)


def test_build_notifier_requires_slack_env_when_posting(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_USER_ID", raising=False)
    ctx = _ctx(tmp_path)
    with pytest.raises(ApplicationError, match="SLACK_"):
        application.run_pipeline(Cadence.DAILY, ctx=ctx, no_slack=False, dry_run=False)


# ---------------------------------------------------------------- #
# Pipeline result shape — confirms application returns typed result
# ---------------------------------------------------------------- #


def test_run_pipeline_returns_typed_result(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    ctx = _ctx(tmp_path)

    # Stub the graph invocation; we only care that application wires the result.
    from news_agent.core.types import PipelineCounters

    def _fake_build_graph(deps):
        class _G:
            def invoke(self, _state):
                return {
                    "raw_articles": [1, 2, 3],
                    "new_articles": [1, 2],
                    "scored": [],
                    "score_results": [1],
                    "digest_items": [("art", "score")],
                    "priority_items": [],
                    "surface_refs": ["ref-1"],
                    "counters": PipelineCounters(),
                }
        return _G()

    monkeypatch.setattr("news_agent.application.build_graph", _fake_build_graph)

    result: PipelineResult = application.run_pipeline(
        Cadence.DAILY, ctx=ctx, dry_run=True, no_slack=True,
    )
    assert result.cadence is Cadence.DAILY
    assert result.fetched == 3
    assert result.new == 2
    assert result.scored == 1
    assert len(result.digest_items) == 1
    assert result.posted == 1
    assert result.dry_run is True


# ---------------------------------------------------------------- #
# handle_correction persists + updates priors
# ---------------------------------------------------------------- #


def test_handle_correction_persists_and_updates_prior(tmp_path):
    from news_agent.core.types import Article, ContentHash
    from news_agent.storage.repository import (
        connect,
        get_source_prior,
        init_db,
        upsert_article,
    )

    db = tmp_path / "t.db"
    init_db(db)

    # Corrections FK to articles — seed the referenced article first.
    seed_conn = connect(db)
    try:
        now = datetime.now(timezone.utc)
        upsert_article(seed_conn, Article(
            id=ArticleId("fakesrc:abc"),
            source=SourceId("fakesrc"),
            url="https://x/a",
            title="A",
            body="b",
            content_hash=ContentHash("abc000000000"),
            published_at=now,
            fetched_at=now,
        ))
        seed_conn.commit()
    finally:
        seed_conn.close()

    event = CorrectionEvent(
        article=ArticleId("fakesrc:abc"),
        topic=TopicId("topic_a"),
        source=SourceId("fakesrc"),
        kind=CorrectionKind.BOOST,
        surface=SurfaceRef(
            surface="slack",
            channel="D0X",
            message_id="ts-1",
            posted_at=datetime.now(timezone.utc),
        ),
        user="U-1",
        at=datetime.now(timezone.utc),
    )
    outcome: CorrectionOutcome = application.handle_correction(
        event, db_path=db, log=lambda _msg: None,
    )

    assert outcome.event == event
    assert outcome.prior_before == 0.5  # initial baseline
    assert outcome.prior_after > outcome.prior_before  # BOOST nudges up

    # Verify state persisted.
    conn = connect(db)
    try:
        prior = get_source_prior(conn, SourceId("fakesrc"), TopicId("topic_a"))
        assert prior == pytest.approx(outcome.prior_after)
    finally:
        conn.close()
