"""Pure routing logic — partition scored articles into digest vs priority.

The graph node holds no logic itself; it pre-fetches the prior-priority-surface
set from the DB and calls route_to_surface() to do the actual partition,
dedup, quality filter, and cap. Tests drive this function directly without a
graph or sqlite seam.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..config.schema import TopicsConfig
from ..core.types import Article, Cadence, ScoreResult


DIGEST_CAP = 3


@dataclass(frozen=True)
class RouteResult:
    digest_items: list[tuple[Article, ScoreResult]]
    priority_items: list[tuple[Article, ScoreResult]]


def route_to_surface(
    *,
    score_results: list[ScoreResult],
    articles_by_id: dict[str, Article],
    topics_cfg: TopicsConfig,
    cadence: Cadence,
    now: datetime,
    prior_priority_surfaces: frozenset[tuple[str, str]] = frozenset(),
) -> RouteResult:
    """Split scored (article, topic) pairs into digest and priority buckets.

    Rules:
      - Priority candidate: final ≥ topic.delivery.priority_threshold AND
        age ≤ topic.delivery.priority_recency_hours.
      - Priority dedup: drop pairs whose (article_id, topic_id) is already in
        prior_priority_surfaces (relevant for hourly priority runs).
      - PRIORITY cadence: digest is empty by design (digest cadence handles it).
      - DAILY cadence:
          * Apply topic.delivery.digest_min_score as quality floor.
          * Sort surviving by final desc.
          * Keep up to DIGEST_CAP globally.
      - Pairs with unknown article or topic_cfg are silently dropped — the
        caller is responsible for ensuring referential integrity.
    """
    digest: list[tuple[Article, ScoreResult]] = []
    priority: list[tuple[Article, ScoreResult]] = []

    for sr in score_results:
        article = articles_by_id.get(str(sr.article))
        topic_cfg = topics_cfg.topics.get(str(sr.topic))
        if article is None or topic_cfg is None:
            continue
        age_h = (now - article.published_at).total_seconds() / 3600
        delivery = topic_cfg.delivery
        if sr.final >= delivery.priority_threshold and age_h <= delivery.priority_recency_hours:
            priority.append((article, sr))
        else:
            digest.append((article, sr))

    priority = [
        item for item in priority
        if (str(item[1].article), str(item[1].topic)) not in prior_priority_surfaces
    ]

    if cadence is Cadence.PRIORITY:
        return RouteResult(digest_items=[], priority_items=priority)

    quality_candidates: list[tuple[Article, ScoreResult]] = []
    for item in digest:
        topic_cfg = topics_cfg.topics.get(str(item[1].topic))
        if topic_cfg is None:
            continue
        if item[1].final < topic_cfg.delivery.digest_min_score:
            continue
        quality_candidates.append(item)
    quality_candidates.sort(key=lambda x: x[1].final, reverse=True)
    chosen = quality_candidates[:DIGEST_CAP]

    return RouteResult(digest_items=chosen, priority_items=priority)
