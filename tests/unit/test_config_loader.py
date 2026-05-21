"""Smoke tests: shipped configs parse cleanly under the schema."""

from __future__ import annotations

from pathlib import Path

from news_agent.config.loader import load_priors, load_sources, load_tags, load_topics

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def test_tags_loads():
    cfg = load_tags(CONFIG_DIR)
    assert "domain" in cfg.tags
    assert "type" in cfg.tags
    assert "quality" in cfg.tags


def test_topics_loads():
    cfg = load_topics(CONFIG_DIR)
    assert isinstance(cfg.topics, dict)
    for topic_id, topic in cfg.topics.items():
        assert topic.label
        assert topic.recency.half_life_days > 0


def test_sources_loads():
    cfg = load_sources(CONFIG_DIR)
    assert isinstance(cfg.sources, list)
    for src in cfg.sources:
        assert src.id
        assert src.type
        assert 0 <= src.weight <= 1


def test_priors_loads():
    cfg = load_priors(CONFIG_DIR)
    assert isinstance(cfg.priors, list)


def test_sources_reference_existing_topics():
    """Every topic listed by a source must exist in topics.yaml."""
    topic_ids = set(load_topics(CONFIG_DIR).topics)
    for src in load_sources(CONFIG_DIR).sources:
        for t in src.topics:
            assert t in topic_ids, f"source {src.id} references unknown topic {t}"
