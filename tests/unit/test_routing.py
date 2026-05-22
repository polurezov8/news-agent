"""Direct unit tests for the route_to_surface pure function.

No graph, no sqlite. Builds ScoreResults + Articles + topics in-memory and
asserts on digest/priority split, dedup, cap, and quality floor.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from news_agent.config.schema import (
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
    ScoreResult,
    SourceId,
    TopicId,
)
from news_agent.pipeline.routing import DIGEST_CAP, route_to_surface


NOW = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)


def _topic(
    digest_min_score: float = 0.4,
    priority_threshold: float = 0.9,
    priority_recency_hours: int = 24,
) -> TopicConfig:
    return TopicConfig(
        label="AI",
        emoji="🤖",
        query=TopicQuery(must_have_any=["ai"]),
        recency=TopicRecency(half_life_days=7),
        delivery=TopicDelivery(
            digest_min_score=digest_min_score,
            priority_threshold=priority_threshold,
            priority_recency_hours=priority_recency_hours,
        ),
        nl_rules=[],
    )


def _topics(**topics: TopicConfig) -> TopicsConfig:
    return TopicsConfig(topics=topics)


def _article(aid: str, *, age_hours: float = 1.0) -> Article:
    return Article(
        id=ArticleId(f"src:{aid}"),
        source=SourceId("src"),
        url=f"https://x/{aid}",
        title=f"Article {aid}",
        body="b",
        content_hash=ContentHash(aid.ljust(12, "0")),
        published_at=NOW - timedelta(hours=age_hours),
        fetched_at=NOW,
    )


def _score(article: Article, topic_id: str, final: float) -> ScoreResult:
    return ScoreResult(
        article=article.id,
        topic=TopicId(topic_id),
        substance=final,
        tag_adj=0.0,
        decay=1.0,
        source_weight=1.0,
        final=final,
    )


# ---------------------------------------------------------------- #
# Priority split: threshold + recency
# ---------------------------------------------------------------- #


def test_high_score_recent_goes_to_priority():
    a = _article("a", age_hours=1.0)
    sr = _score(a, "ai", final=0.95)
    result = route_to_surface(
        score_results=[sr],
        articles_by_id={str(a.id): a},
        topics_cfg=_topics(ai=_topic(priority_threshold=0.9, priority_recency_hours=24)),
        cadence=Cadence.DAILY,
        now=NOW,
    )
    assert len(result.priority_items) == 1
    assert result.digest_items == []


def test_high_score_stale_goes_to_digest_not_priority():
    """Above threshold but past recency window → digest only."""
    a = _article("old", age_hours=48.0)  # > 24h recency
    sr = _score(a, "ai", final=0.95)
    result = route_to_surface(
        score_results=[sr],
        articles_by_id={str(a.id): a},
        topics_cfg=_topics(ai=_topic(priority_threshold=0.9, priority_recency_hours=24)),
        cadence=Cadence.DAILY,
        now=NOW,
    )
    assert result.priority_items == []
    assert len(result.digest_items) == 1


def test_below_threshold_goes_to_digest():
    a = _article("mid")
    sr = _score(a, "ai", final=0.85)  # below 0.9 threshold
    result = route_to_surface(
        score_results=[sr],
        articles_by_id={str(a.id): a},
        topics_cfg=_topics(ai=_topic(priority_threshold=0.9)),
        cadence=Cadence.DAILY,
        now=NOW,
    )
    assert result.priority_items == []
    assert len(result.digest_items) == 1


# ---------------------------------------------------------------- #
# Priority dedup against prior surfaces
# ---------------------------------------------------------------- #


def test_priority_dedup_drops_prior_surface():
    a = _article("seen")
    sr = _score(a, "ai", final=0.99)
    prior = frozenset({(str(a.id), "ai")})
    result = route_to_surface(
        score_results=[sr],
        articles_by_id={str(a.id): a},
        topics_cfg=_topics(ai=_topic()),
        cadence=Cadence.PRIORITY,
        now=NOW,
        prior_priority_surfaces=prior,
    )
    assert result.priority_items == []
    assert result.digest_items == []


# ---------------------------------------------------------------- #
# Cadence behavior
# ---------------------------------------------------------------- #


def test_priority_cadence_empties_digest_even_if_qualifying():
    """Two items qualify for digest; PRIORITY cadence drops the digest list."""
    a = _article("digest_a")
    b = _article("digest_b")
    srs = [_score(a, "ai", final=0.5), _score(b, "ai", final=0.6)]
    result = route_to_surface(
        score_results=srs,
        articles_by_id={str(a.id): a, str(b.id): b},
        topics_cfg=_topics(ai=_topic(digest_min_score=0.4)),
        cadence=Cadence.PRIORITY,
        now=NOW,
    )
    assert result.digest_items == []
    # Priority list is also empty here (both below priority_threshold).
    assert result.priority_items == []


# ---------------------------------------------------------------- #
# Daily digest quality floor + cap
# ---------------------------------------------------------------- #


def test_daily_digest_drops_below_min_score():
    a = _article("weak")
    sr = _score(a, "ai", final=0.2)  # below default 0.4
    result = route_to_surface(
        score_results=[sr],
        articles_by_id={str(a.id): a},
        topics_cfg=_topics(ai=_topic(digest_min_score=0.4)),
        cadence=Cadence.DAILY,
        now=NOW,
    )
    assert result.digest_items == []


def test_daily_digest_hard_capped():
    articles = [_article(f"item-{i:02d}") for i in range(5)]
    srs = [_score(art, "ai", final=0.7) for art in articles]
    result = route_to_surface(
        score_results=srs,
        articles_by_id={str(a.id): a for a in articles},
        topics_cfg=_topics(ai=_topic()),
        cadence=Cadence.DAILY,
        now=NOW,
    )
    assert len(result.digest_items) == DIGEST_CAP


def test_daily_digest_sorted_by_final_desc():
    a = _article("low")
    b = _article("mid")
    c = _article("high")
    srs = [
        _score(a, "ai", final=0.5),
        _score(b, "ai", final=0.7),
        _score(c, "ai", final=0.9),
    ]
    result = route_to_surface(
        score_results=srs,
        articles_by_id={str(a.id): a, str(b.id): b, str(c.id): c},
        topics_cfg=_topics(ai=_topic(priority_threshold=0.95)),
        cadence=Cadence.DAILY,
        now=NOW,
    )
    finals = [sr.final for _art, sr in result.digest_items]
    assert finals == sorted(finals, reverse=True)
    assert finals[0] == 0.9


# ---------------------------------------------------------------- #
# Referential integrity — unknown article or topic
# ---------------------------------------------------------------- #


def test_unknown_article_silently_dropped():
    a = _article("present")
    sr_present = _score(a, "ai", final=0.7)
    # Score result for an article not in articles_by_id.
    a_ghost = _article("ghost")
    sr_ghost = _score(a_ghost, "ai", final=0.7)
    result = route_to_surface(
        score_results=[sr_present, sr_ghost],
        articles_by_id={str(a.id): a},  # only the present one
        topics_cfg=_topics(ai=_topic()),
        cadence=Cadence.DAILY,
        now=NOW,
    )
    assert len(result.digest_items) == 1


def test_unknown_topic_silently_dropped():
    a = _article("a")
    sr = _score(a, "missing_topic", final=0.95)
    result = route_to_surface(
        score_results=[sr],
        articles_by_id={str(a.id): a},
        topics_cfg=_topics(ai=_topic()),
        cadence=Cadence.DAILY,
        now=NOW,
    )
    assert result.digest_items == []
    assert result.priority_items == []
