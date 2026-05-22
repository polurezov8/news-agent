"""Pure helpers for the pipeline's filter and score stages.

filter_topics and score_node nodes were inlining tag→topic matching and
match→topic batching inside closures. Extracting them gives those algorithms
a name and a test surface independent of the LangGraph machinery.
"""

from __future__ import annotations

from ..config.schema import TopicsConfig
from ..core.types import Article, Tag
from .scoring import matches_topic


def match_articles_to_topics(
    tag_map: dict[str, frozenset[Tag]],
    articles_by_id: dict[str, Article],
    source_topics: dict[str, list[str]],
    topics_cfg: TopicsConfig,
) -> list[tuple[str, str]]:
    """For each tagged article, emit (article_id, topic_id) pairs where:
      - the article's source declares it cares about topic_id, AND
      - the article's tags match the topic's query (matches_topic).

    Unknown topics or articles are silently dropped — callers maintain the
    referential integrity of tag_map vs articles_by_id.
    """
    matches: list[tuple[str, str]] = []
    for article_id, tags in tag_map.items():
        article = articles_by_id.get(article_id)
        if article is None:
            continue
        for topic_id in source_topics.get(str(article.source), []):
            topic_cfg = topics_cfg.topics.get(topic_id)
            if topic_cfg is None:
                continue
            if matches_topic(tags, topic_cfg):
                matches.append((article_id, topic_id))
    return matches


def group_matches_by_topic(
    matches: list[tuple[str, str]],
    articles_by_id: dict[str, Article],
    tag_map: dict[str, frozenset[Tag]],
) -> dict[str, list[tuple[str, Article, frozenset[Tag]]]]:
    """Reshape flat (article_id, topic_id) pairs into per-topic batches.

    The score stage issues one LLM call per topic with the full article+tag
    batch, so grouping by topic before that step is required.

    Pairs whose article is missing from articles_by_id are silently skipped.
    """
    by_topic: dict[str, list[tuple[str, Article, frozenset[Tag]]]] = {}
    for article_id, topic_id in matches:
        article = articles_by_id.get(article_id)
        if article is None:
            continue
        tags = tag_map.get(article_id, frozenset())
        by_topic.setdefault(topic_id, []).append((article_id, article, tags))
    return by_topic
