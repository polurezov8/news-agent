"""Direct unit tests for match_articles_to_topics and group_matches_by_topic."""

from __future__ import annotations

from datetime import datetime, timezone

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
    ContentHash,
    SourceId,
    Tag,
)
from news_agent.pipeline.matching import (
    group_matches_by_topic,
    match_articles_to_topics,
)


NOW = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)


def _topic(must_have_any: list[str]) -> TopicConfig:
    return TopicConfig(
        label="AI",
        emoji="🤖",
        query=TopicQuery(must_have_any=must_have_any),
        recency=TopicRecency(half_life_days=7),
        delivery=TopicDelivery(),
        nl_rules=[],
    )


def _article(aid: str, source: str = "hn") -> Article:
    return Article(
        id=ArticleId(f"{source}:{aid}"),
        source=SourceId(source),
        url=f"https://x/{aid}",
        title=f"Article {aid}",
        body="b",
        content_hash=ContentHash(aid.ljust(12, "0")),
        published_at=NOW,
        fetched_at=NOW,
    )


# ---------------------------------------------------------------- #
# match_articles_to_topics
# ---------------------------------------------------------------- #


def test_match_emits_pair_when_tags_and_source_align():
    art = _article("a", source="hn")
    matches = match_articles_to_topics(
        tag_map={str(art.id): frozenset([Tag("ai")])},
        articles_by_id={str(art.id): art},
        source_topics={"hn": ["topic_a"]},
        topics_cfg=TopicsConfig(topics={"topic_a": _topic(must_have_any=["ai"])}),
    )
    assert matches == [(str(art.id), "topic_a")]


def test_match_skips_when_tags_dont_match_topic():
    art = _article("a", source="hn")
    matches = match_articles_to_topics(
        tag_map={str(art.id): frozenset([Tag("food")])},
        articles_by_id={str(art.id): art},
        source_topics={"hn": ["topic_a"]},
        topics_cfg=TopicsConfig(topics={"topic_a": _topic(must_have_any=["ai"])}),
    )
    assert matches == []


def test_match_skips_when_source_not_subscribed_to_topic():
    """Article's source has no topics declared → no matches even if tags would qualify."""
    art = _article("a", source="lobsters")
    matches = match_articles_to_topics(
        tag_map={str(art.id): frozenset([Tag("ai")])},
        articles_by_id={str(art.id): art},
        source_topics={"hn": ["topic_a"]},  # lobsters absent
        topics_cfg=TopicsConfig(topics={"topic_a": _topic(must_have_any=["ai"])}),
    )
    assert matches == []


def test_match_emits_multiple_topics_per_article_when_source_subscribed_to_many():
    art = _article("a", source="hn")
    matches = match_articles_to_topics(
        tag_map={str(art.id): frozenset([Tag("ai"), Tag("infra")])},
        articles_by_id={str(art.id): art},
        source_topics={"hn": ["topic_ai", "topic_infra", "topic_food"]},
        topics_cfg=TopicsConfig(topics={
            "topic_ai": _topic(must_have_any=["ai"]),
            "topic_infra": _topic(must_have_any=["infra"]),
            "topic_food": _topic(must_have_any=["food"]),
        }),
    )
    matched_topics = {tid for _aid, tid in matches}
    assert matched_topics == {"topic_ai", "topic_infra"}


def test_match_skips_unknown_article_in_tag_map():
    matches = match_articles_to_topics(
        tag_map={"ghost-id": frozenset([Tag("ai")])},
        articles_by_id={},
        source_topics={"hn": ["topic_a"]},
        topics_cfg=TopicsConfig(topics={"topic_a": _topic(must_have_any=["ai"])}),
    )
    assert matches == []


# ---------------------------------------------------------------- #
# group_matches_by_topic
# ---------------------------------------------------------------- #


def test_group_buckets_pairs_by_topic_id():
    a = _article("a")
    b = _article("b")
    c = _article("c")
    grouped = group_matches_by_topic(
        matches=[
            (str(a.id), "topic_x"),
            (str(b.id), "topic_x"),
            (str(c.id), "topic_y"),
        ],
        articles_by_id={str(a.id): a, str(b.id): b, str(c.id): c},
        tag_map={
            str(a.id): frozenset([Tag("ai")]),
            str(b.id): frozenset([Tag("ai")]),
            str(c.id): frozenset([Tag("infra")]),
        },
    )
    assert sorted(grouped.keys()) == ["topic_x", "topic_y"]
    assert len(grouped["topic_x"]) == 2
    assert len(grouped["topic_y"]) == 1


def test_group_includes_tags_per_pair():
    a = _article("a")
    grouped = group_matches_by_topic(
        matches=[(str(a.id), "topic_x")],
        articles_by_id={str(a.id): a},
        tag_map={str(a.id): frozenset([Tag("ai"), Tag("essay")])},
    )
    _aid, article, tags = grouped["topic_x"][0]
    assert article == a
    assert tags == frozenset([Tag("ai"), Tag("essay")])


def test_group_drops_pairs_with_missing_article():
    a = _article("a")
    grouped = group_matches_by_topic(
        matches=[(str(a.id), "topic_x"), ("ghost", "topic_x")],
        articles_by_id={str(a.id): a},
        tag_map={str(a.id): frozenset()},
    )
    assert len(grouped["topic_x"]) == 1


def test_group_returns_empty_dict_for_no_matches():
    assert group_matches_by_topic(matches=[], articles_by_id={}, tag_map={}) == {}
