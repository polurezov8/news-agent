"""Integration test for the LangGraph pipeline.

Stubs source registry, LLM clients, and notifier so no network or API calls happen.
Proves all node return-keys connect correctly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

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
    Article,
    ArticleId,
    Cadence,
    ContentHash,
    SourceId,
    SurfaceRef,
    Tag,
    TagResult,
    TopicId,
)


@dataclass
class _RecordingNotifier:
    digests: list = field(default_factory=list)
    priorities: list = field(default_factory=list)

    def post_digest(self, payload) -> SurfaceRef:
        self.digests.append(payload)
        return SurfaceRef(
            surface="slack", channel="D0X", message_id=f"d-{len(self.digests)}",
            posted_at=datetime.now(timezone.utc),
        )

    def dm_priority(self, payload) -> SurfaceRef:
        self.priorities.append(payload)
        return SurfaceRef(
            surface="slack", channel="D0X", message_id=f"p-{len(self.priorities)}",
            posted_at=datetime.now(timezone.utc),
        )

    def post_recap(self, payload) -> SurfaceRef:
        return SurfaceRef(surface="slack", channel="D0X", message_id="r-1", posted_at=datetime.now(timezone.utc))

    def handle_correction(self, ev) -> None:
        pass


def _article(aid: str, title: str = "T") -> Article:
    now = datetime.now(timezone.utc)
    return Article(
        id=ArticleId(aid),
        source=SourceId(aid.split(":")[0]),
        url="https://example.com",
        title=title,
        body="body",
        content_hash=ContentHash(aid.split(":")[1]),
        published_at=now,
        fetched_at=now,
    )


@pytest.fixture
def configs():
    tags = TagsConfig(tags={"domain": ["ai"], "type": ["essay"]})
    topics = TopicsConfig(topics={
        "topic_a": TopicConfig(
            label="AI",
            emoji="🤖",
            query=TopicQuery(must_have_any=["ai"]),
            recency=TopicRecency(half_life_days=7),
            delivery=TopicDelivery(priority_threshold=0.95, priority_recency_hours=24, digest_top_n=3),
            nl_rules=[],
        ),
    })
    sources = SourcesConfig(sources=[
        SourceConfig(id="fakesrc", type="_fake_test", config={}, topics=["topic_a"], weight=1.0),
    ])
    priors = PriorsConfig(priors=[PriorEntry(source="fakesrc", topic="topic_a", weight=1.0)])
    return tags, topics, sources, priors


def test_pipeline_end_to_end(monkeypatch, tmp_path: Path, configs):
    tags_cfg, topics_cfg, sources_cfg, priors_cfg = configs
    db_path = tmp_path / "t.db"

    from news_agent.storage.repository import init_db

    init_db(db_path)

    fetched_articles = [
        _article("fakesrc:0000000000aaaaaa", title="AI essay one"),
        _article("fakesrc:0000000000bbbbbb", title="Off-topic note"),
    ]

    class _FakeSource:
        id = SourceId("fakesrc")
        topics = [Tag("topic_a")]
        weight = 1.0

        def fetch(self):
            return fetched_articles

    monkeypatch.setattr(
        "news_agent.pipeline.graph.make_source", lambda _cfg: _FakeSource(),
    )

    # First article gets "ai" tag → matches topic. Second gets nothing → filtered out.
    def _fake_tag_articles(articles, cfg, *, model, client=None):
        out = []
        for a in articles:
            if "AI" in a.title:
                out.append(TagResult(
                    article=a.id, tags=frozenset([Tag("ai"), Tag("essay")]),
                    confidence=1.0, model=model,
                ))
            else:
                out.append(TagResult(
                    article=a.id, tags=frozenset(), confidence=1.0, model=model,
                ))
        return out

    monkeypatch.setattr(
        "news_agent.llm.tagger.tag_articles", _fake_tag_articles,
    )

    monkeypatch.setattr(
        "news_agent.llm.scorer.score_batch_for_topic",
        lambda articles_tags, topic, *, model, client=None: [0.9] * len(articles_tags),
    )
    monkeypatch.setattr(
        "news_agent.llm.scorer._client", lambda model: object(),
    )

    notifier = _RecordingNotifier()

    from news_agent.pipeline.graph import PipelineDeps, build_graph, empty_state

    deps = PipelineDeps(
        sources_cfg=sources_cfg,
        topics_cfg=topics_cfg,
        tags_cfg=tags_cfg,
        priors_cfg=priors_cfg,
        db_path=db_path,
        notifier=notifier,
        tagger_model="haiku-test",
        scorer_model="sonnet-test",
        log=lambda msg: None,
    )
    graph = build_graph(deps)
    state = graph.invoke(empty_state())

    assert len(state["raw_articles"]) == 2
    assert len(state["new_articles"]) == 2          # both fresh
    assert "fakesrc:0000000000aaaaaa" in state["tag_map"]
    assert len(state["matches"]) == 1               # only the AI essay matches
    assert len(state["score_results"]) == 1
    sr = state["score_results"][0]
    assert sr.substance == 0.9
    # final = (0.9 + 0) * decay(~1.0) * 1.0 ≈ 0.9 → below 0.95 threshold → goes to digest
    assert len(state["digest_items"]) == 1
    assert len(state["priority_items"]) == 0
    assert len(state["surface_refs"]) == 1
    assert len(notifier.digests) == 1
    assert len(notifier.priorities) == 0

    counters = state["counters"]
    assert counters.fetched == 2
    assert counters.new == 2
    assert counters.tagged == 2
    assert counters.on_topic == 1
    assert counters.scored == 1
    assert counters.surfaced == 1
    # Notifier received counters in payload.
    assert notifier.digests[0].counters == counters


def test_pipeline_with_no_slack_skips_post(monkeypatch, tmp_path: Path, configs):
    tags_cfg, topics_cfg, sources_cfg, priors_cfg = configs
    db_path = tmp_path / "t.db"

    from news_agent.storage.repository import init_db

    init_db(db_path)

    class _Src:
        id = SourceId("fakesrc")
        topics = [Tag("topic_a")]
        weight = 1.0

        def fetch(self):
            return [_article("fakesrc:0000000000cccccc", title="AI ok")]

    monkeypatch.setattr("news_agent.pipeline.graph.make_source", lambda _: _Src())
    monkeypatch.setattr(
        "news_agent.llm.tagger.tag_articles",
        lambda articles, cfg, *, model, client=None: [
            TagResult(article=a.id, tags=frozenset([Tag("ai")]), confidence=1.0, model=model)
            for a in articles
        ],
    )
    monkeypatch.setattr(
        "news_agent.llm.scorer.score_batch_for_topic",
        lambda articles_tags, topic, **k: [0.7] * len(articles_tags),
    )
    monkeypatch.setattr("news_agent.llm.scorer._client", lambda m: object())

    from news_agent.pipeline.graph import PipelineDeps, build_graph, empty_state

    deps = PipelineDeps(
        sources_cfg=sources_cfg, topics_cfg=topics_cfg,
        tags_cfg=tags_cfg, priors_cfg=priors_cfg,
        db_path=db_path, notifier=None,
        log=lambda msg: None,
    )
    state = build_graph(deps).invoke(empty_state())

    assert state["surface_refs"] == []
    assert len(state["score_results"]) == 1


def test_priority_cadence_suppresses_digest_and_dedups_via_surfaces(
    monkeypatch, tmp_path: Path, configs,
):
    tags_cfg, topics_cfg, sources_cfg, priors_cfg = configs
    db_path = tmp_path / "t.db"

    from news_agent.storage.repository import (
        connect, init_db, save_surface, upsert_article,
    )

    init_db(db_path)

    # Pre-seed a "previously DM'd" article to confirm dedup skips it.
    prev = _article("fakesrc:0000000000eeeeee", title="AI old hot")
    conn = connect(db_path)
    try:
        upsert_article(conn, prev)
        save_surface(
            conn, prev.id, TopicId("topic_a"),
            SurfaceRef(
                surface="slack", channel="D0X", message_id="p-prev",
                posted_at=datetime.now(timezone.utc),
            ),
            Cadence.PRIORITY,
        )
        conn.commit()
    finally:
        conn.close()

    fresh = _article("fakesrc:0000000000ffffff", title="AI breaking news")

    class _Src:
        id = SourceId("fakesrc")
        topics = [Tag("topic_a")]
        weight = 1.0

        def fetch(self):
            return [prev, fresh]

    monkeypatch.setattr("news_agent.pipeline.graph.make_source", lambda _: _Src())
    monkeypatch.setattr(
        "news_agent.llm.tagger.tag_articles",
        lambda articles, cfg, *, model, client=None: [
            TagResult(article=a.id, tags=frozenset([Tag("ai")]), confidence=1.0, model=model)
            for a in articles
        ],
    )
    # Both score very high, above 0.95 threshold → both qualify as priority.
    monkeypatch.setattr("news_agent.llm.scorer.score_batch_for_topic", lambda items, topic, **k: [0.99] * len(items))
    monkeypatch.setattr("news_agent.llm.scorer._client", lambda m: object())

    notifier = _RecordingNotifier()

    from news_agent.pipeline.graph import PipelineDeps, build_graph, empty_state

    deps = PipelineDeps(
        sources_cfg=sources_cfg, topics_cfg=topics_cfg,
        tags_cfg=tags_cfg, priors_cfg=priors_cfg,
        db_path=db_path, notifier=notifier,
        cadence=Cadence.PRIORITY,
        log=lambda msg: None,
    )
    state = build_graph(deps).invoke(empty_state())

    # Only the fresh one survives dedup; prev was filtered (already DM'd).
    # `prev` is also filtered by dedup node itself (already in articles).
    assert state["digest_items"] == []
    assert len(state["priority_items"]) <= 1
    # Notifier should have been called for any survivors only via dm_priority.
    assert len(notifier.digests) == 0


def test_notify_silent_when_digest_and_priority_empty(monkeypatch, tmp_path: Path, configs):
    """Empty-state: nothing passes digest_min_score → no post."""
    tags_cfg, topics_cfg, sources_cfg, priors_cfg = configs
    db_path = tmp_path / "t.db"

    from news_agent.storage.repository import init_db

    init_db(db_path)

    class _Src:
        id = SourceId("fakesrc")
        topics = [Tag("topic_a")]
        weight = 1.0

        def fetch(self):
            return [_article("fakesrc:0000000000aaaaaa", title="AI weak signal")]

    monkeypatch.setattr("news_agent.pipeline.graph.make_source", lambda _: _Src())
    monkeypatch.setattr(
        "news_agent.llm.tagger.tag_articles",
        lambda articles, cfg, *, model, client=None: [
            TagResult(article=a.id, tags=frozenset([Tag("ai")]), confidence=1.0, model=model)
            for a in articles
        ],
    )
    # Score below the topic's digest_min_score (default 0.4) → fails quality.
    monkeypatch.setattr(
        "news_agent.llm.scorer.score_batch_for_topic",
        lambda items, topic, **k: [0.1] * len(items),
    )
    monkeypatch.setattr("news_agent.llm.scorer._client", lambda m: object())

    notifier = _RecordingNotifier()
    from news_agent.pipeline.graph import PipelineDeps, build_graph, empty_state

    deps = PipelineDeps(
        sources_cfg=sources_cfg, topics_cfg=topics_cfg,
        tags_cfg=tags_cfg, priors_cfg=priors_cfg,
        db_path=db_path, notifier=notifier,
        log=lambda msg: None,
    )
    state = build_graph(deps).invoke(empty_state())

    assert state["digest_items"] == []
    assert state["priority_items"] == []
    assert state["surface_refs"] == []
    assert notifier.digests == [], "must not post a digest message when empty"
    assert notifier.priorities == [], "must not DM when empty"


def test_daily_digest_capped_at_three(monkeypatch, tmp_path: Path, configs):
    """Daily digest surfaces at most 3 items regardless of how many qualify.
    Topic config sets digest_top_n=10, so any cap below 10 proves the hard global cap."""
    from datetime import timedelta

    tags_cfg, topics_cfg, sources_cfg, priors_cfg = configs
    # Raise per-topic cap to 10 so this test proves the GLOBAL hard cap of 3.
    topics_cfg.topics["topic_a"].delivery.digest_top_n = 10
    db_path = tmp_path / "t.db"

    from news_agent.storage.repository import init_db

    init_db(db_path)

    now = datetime.now(timezone.utc)
    five_articles = [
        Article(
            id=ArticleId(f"fakesrc:000000000000aa{i:02d}"),
            source=SourceId("fakesrc"),
            url=f"https://x/item{i}",
            title=f"AI item {i}",
            body="b",
            content_hash=ContentHash(f"000000000000aa{i:02d}"),
            published_at=now - timedelta(hours=i),
            fetched_at=now,
        )
        for i in range(5)
    ]

    class _Src:
        id = SourceId("fakesrc")
        topics = [Tag("topic_a")]
        weight = 1.0

        def fetch(self):
            return five_articles

    monkeypatch.setattr("news_agent.pipeline.graph.make_source", lambda _: _Src())
    monkeypatch.setattr(
        "news_agent.llm.tagger.tag_articles",
        lambda articles, cfg, *, model, client=None: [
            TagResult(article=a.id, tags=frozenset([Tag("ai")]), confidence=1.0, model=model)
            for a in articles
        ],
    )
    # All score well above min_score 0.4 → all 5 eligible for quality fill.
    monkeypatch.setattr(
        "news_agent.llm.scorer.score_batch_for_topic",
        lambda items, topic, **k: [0.9] * len(items),
    )
    monkeypatch.setattr("news_agent.llm.scorer._client", lambda m: object())

    notifier = _RecordingNotifier()
    from news_agent.pipeline.graph import PipelineDeps, build_graph, empty_state

    deps = PipelineDeps(
        sources_cfg=sources_cfg, topics_cfg=topics_cfg,
        tags_cfg=tags_cfg, priors_cfg=priors_cfg,
        db_path=db_path, notifier=notifier,
        log=lambda msg: None,
    )
    state = build_graph(deps).invoke(empty_state())

    assert len(state["digest_items"]) == 3, "daily digest is hard-capped at 3 picks"


def test_dedup_skips_already_persisted(monkeypatch, tmp_path: Path, configs):
    tags_cfg, topics_cfg, sources_cfg, priors_cfg = configs
    db_path = tmp_path / "t.db"

    from news_agent.storage.repository import (
        connect,
        init_db,
        upsert_article,
    )

    init_db(db_path)
    seeded = _article("fakesrc:0000000000dddddd", title="Already here")
    conn = connect(db_path)
    try:
        upsert_article(conn, seeded)
        conn.commit()
    finally:
        conn.close()

    class _Src:
        id = SourceId("fakesrc")
        topics = [Tag("topic_a")]
        weight = 1.0

        def fetch(self):
            return [seeded, _article("fakesrc:0000000000eeeeee", title="AI new")]

    monkeypatch.setattr("news_agent.pipeline.graph.make_source", lambda _: _Src())
    monkeypatch.setattr(
        "news_agent.llm.tagger.tag_articles",
        lambda articles, cfg, *, model, client=None: [
            TagResult(article=a.id, tags=frozenset([Tag("ai")]), confidence=1.0, model=model)
            for a in articles
        ],
    )
    monkeypatch.setattr("news_agent.llm.scorer.score_batch_for_topic", lambda items, topic, **k: [0.5] * len(items))
    monkeypatch.setattr("news_agent.llm.scorer._client", lambda m: object())

    from news_agent.pipeline.graph import PipelineDeps, build_graph, empty_state

    deps = PipelineDeps(
        sources_cfg=sources_cfg, topics_cfg=topics_cfg,
        tags_cfg=tags_cfg, priors_cfg=priors_cfg,
        db_path=db_path, notifier=_RecordingNotifier(),
        log=lambda msg: None,
    )
    state = build_graph(deps).invoke(empty_state())

    assert len(state["raw_articles"]) == 2
    assert len(state["new_articles"]) == 1                  # seeded one filtered out
    assert state["new_articles"][0].title == "AI new"
