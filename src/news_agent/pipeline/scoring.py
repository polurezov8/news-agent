"""Pure scoring math. No LLM, no IO — fully testable."""

from __future__ import annotations

import math
from datetime import datetime, timezone

from news_agent.config.schema import TagsConfig, TopicConfig
from news_agent.core.types import Tag


def matches_topic(tags: frozenset[Tag], topic: TopicConfig) -> bool:
    """True if tags satisfy the topic's must_have_any and must_not_have constraints."""
    q = topic.query
    if q.must_have_any and not any(Tag(t) in tags for t in q.must_have_any):
        return False
    if q.must_not_have and any(Tag(t) in tags for t in q.must_not_have):
        return False
    return True


def tag_adjustment(tags: frozenset[Tag], topic: TopicConfig) -> float:
    """Sum of matching boost weights + matching penalize weights (already negative)."""
    adj = 0.0
    for rule in topic.query.boost:
        if any(Tag(t) in tags for t in rule.tags):
            adj += rule.weight
    for rule in topic.query.penalize:
        if any(Tag(t) in tags for t in rule.tags):
            adj += rule.weight
    return adj


def decay_factor(published_at: datetime, half_life_days: int) -> float:
    """Exponential decay. Value 1.0 at age 0, 0.5 at half_life_days, etc."""
    if half_life_days <= 0:
        return 1.0
    now = datetime.now(timezone.utc)
    age_days = max(0.0, (now - published_at).total_seconds() / 86400)
    return math.exp(-math.log(2) * age_days / half_life_days)


def compute_final(substance: float, tag_adj: float, decay: float, source_weight: float) -> float:
    """final = (substance + tag_adj) * decay * source_weight, clamped to [0, 1]."""
    return max(0.0, min(1.0, (substance + tag_adj) * decay * source_weight))


def tag_to_category(cfg: TagsConfig) -> dict[str, str]:
    """Flatten TagsConfig into a tag→category lookup."""
    out: dict[str, str] = {}
    for category, tags in cfg.tags.items():
        for t in tags:
            out[t] = category
    return out
