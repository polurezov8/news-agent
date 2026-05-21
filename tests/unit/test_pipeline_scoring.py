from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from news_agent.config.schema import (
    BoostRule,
    TagsConfig,
    TopicConfig,
    TopicDelivery,
    TopicQuery,
    TopicRecency,
)
from news_agent.core.types import Tag
from news_agent.pipeline.scoring import (
    compute_final,
    decay_factor,
    matches_topic,
    tag_adjustment,
    tag_to_category,
)


def _topic(
    *,
    must_have_any=None,
    must_not_have=None,
    boost=None,
    penalize=None,
    half_life=7,
) -> TopicConfig:
    return TopicConfig(
        label="Test",
        emoji="🧪",
        query=TopicQuery(
            must_have_any=must_have_any or [],
            must_not_have=must_not_have or [],
            boost=[BoostRule(tags=tags, weight=w) for tags, w in (boost or [])],
            penalize=[BoostRule(tags=tags, weight=w) for tags, w in (penalize or [])],
        ),
        recency=TopicRecency(half_life_days=half_life),
        delivery=TopicDelivery(),
        nl_rules=[],
    )


class TestMatchesTopic:
    def test_must_have_any_matched(self):
        topic = _topic(must_have_any=["ai", "ml"])
        assert matches_topic(frozenset([Tag("ai")]), topic) is True

    def test_must_have_any_not_matched(self):
        topic = _topic(must_have_any=["ai", "ml"])
        assert matches_topic(frozenset([Tag("ios")]), topic) is False

    def test_must_not_have_blocks(self):
        topic = _topic(must_have_any=["ai"], must_not_have=["promo"])
        assert matches_topic(frozenset([Tag("ai"), Tag("promo")]), topic) is False

    def test_must_not_have_passes_when_absent(self):
        topic = _topic(must_have_any=["ai"], must_not_have=["promo"])
        assert matches_topic(frozenset([Tag("ai")]), topic) is True

    def test_no_constraints_matches_anything(self):
        topic = _topic()
        assert matches_topic(frozenset(), topic) is True
        assert matches_topic(frozenset([Tag("anything")]), topic) is True


class TestTagAdjustment:
    def test_boost_applies_when_tag_present(self):
        topic = _topic(boost=[(["hands_on"], 0.3)])
        assert tag_adjustment(frozenset([Tag("hands_on")]), topic) == pytest.approx(0.3)

    def test_boost_zero_when_tag_absent(self):
        topic = _topic(boost=[(["hands_on"], 0.3)])
        assert tag_adjustment(frozenset([Tag("news")]), topic) == 0.0

    def test_penalize_applies_when_tag_present(self):
        topic = _topic(penalize=[(["promo"], -0.2)])
        assert tag_adjustment(frozenset([Tag("promo")]), topic) == pytest.approx(-0.2)

    def test_boost_and_penalize_sum(self):
        topic = _topic(
            boost=[(["hands_on"], 0.3)],
            penalize=[(["promo"], -0.2)],
        )
        adj = tag_adjustment(frozenset([Tag("hands_on"), Tag("promo")]), topic)
        assert adj == pytest.approx(0.1)

    def test_boost_only_counts_once_per_rule(self):
        topic = _topic(boost=[(["a", "b"], 0.3)])
        adj = tag_adjustment(frozenset([Tag("a"), Tag("b")]), topic)
        assert adj == pytest.approx(0.3)


class TestDecayFactor:
    def test_zero_age_is_one(self):
        now = datetime.now(timezone.utc)
        assert decay_factor(now, 7) == pytest.approx(1.0, abs=0.001)

    def test_half_life_gives_half(self):
        old = datetime.now(timezone.utc) - timedelta(days=7)
        assert decay_factor(old, 7) == pytest.approx(0.5, abs=0.001)

    def test_double_half_life_gives_quarter(self):
        old = datetime.now(timezone.utc) - timedelta(days=14)
        assert decay_factor(old, 7) == pytest.approx(0.25, abs=0.001)

    def test_future_published_clamped_to_one(self):
        future = datetime.now(timezone.utc) + timedelta(days=5)
        assert decay_factor(future, 7) == pytest.approx(1.0, abs=0.001)

    def test_zero_half_life_returns_one(self):
        old = datetime.now(timezone.utc) - timedelta(days=100)
        assert decay_factor(old, 0) == 1.0


class TestComputeFinal:
    def test_clamps_above_one(self):
        assert compute_final(0.9, 0.5, 1.0, 1.0) == 1.0

    def test_clamps_below_zero(self):
        assert compute_final(0.1, -0.5, 1.0, 1.0) == 0.0

    def test_decay_scales(self):
        assert compute_final(0.8, 0.0, 0.5, 1.0) == pytest.approx(0.4)

    def test_source_weight_scales(self):
        assert compute_final(0.8, 0.0, 1.0, 0.5) == pytest.approx(0.4)


class TestTagToCategory:
    def test_flattens_categories(self):
        cfg = TagsConfig(tags={
            "domain": ["ai", "ios"],
            "type": ["essay", "tutorial"],
        })
        lookup = tag_to_category(cfg)
        assert lookup == {
            "ai": "domain",
            "ios": "domain",
            "essay": "type",
            "tutorial": "type",
        }
