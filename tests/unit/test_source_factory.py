from __future__ import annotations

import pytest

from news_agent.config.schema import SourceConfig
from news_agent.sources import SOURCE_REGISTRY, make_source
from news_agent.sources.arxiv import ArxivSource
from news_agent.sources.github_trending import GitHubTrendingSource
from news_agent.sources.hackernews import HackerNewsSource
from news_agent.sources.lobsters import LobstersSource
from news_agent.sources.rss import RssSource
from news_agent.sources.rsshub_twitter_list import RssHubTwitterListSource
from news_agent.sources.safari_reading_list import SafariReadingListSource


def test_registry_populated():
    expected = {
        "rss",
        "hackernews",
        "safari_reading_list",
        "arxiv",
        "github_trending",
        "lobsters",
        "rsshub_twitter_list",
    }
    assert expected.issubset(set(SOURCE_REGISTRY))


def test_make_rss_source():
    cfg = SourceConfig(
        id="my_blog",
        type="rss",
        config={"url": "https://example.com/feed.xml"},
        topics=["topic_a"],
        weight=0.9,
    )
    src = make_source(cfg)
    assert isinstance(src, RssSource)
    assert src.url == "https://example.com/feed.xml"
    assert src.topics == ["topic_a"]


def test_make_hackernews_source():
    cfg = SourceConfig(
        id="hn",
        type="hackernews",
        config={"min_points": 100, "limit": 50},
        topics=["t1", "t2"],
        weight=0.7,
    )
    src = make_source(cfg)
    assert isinstance(src, HackerNewsSource)
    assert src.min_points == 100
    assert src.limit == 50


def test_make_arxiv_source():
    cfg = SourceConfig(
        id="arxiv_cs",
        type="arxiv",
        config={"categories": ["cs.AI", "cs.LG"], "max_results": 25},
        topics=["ai_topic"],
        weight=0.8,
    )
    src = make_source(cfg)
    assert isinstance(src, ArxivSource)
    assert src.categories == ("cs.AI", "cs.LG")
    assert src.max_results == 25


def test_make_github_trending_source():
    cfg = SourceConfig(
        id="gh",
        type="github_trending",
        config={"language": "Swift", "min_stars_today": 50},
        topics=["ios_topic"],
        weight=0.7,
    )
    src = make_source(cfg)
    assert isinstance(src, GitHubTrendingSource)
    assert src.language == "Swift"
    assert src.min_stars_today == 50


def test_make_lobsters_source():
    cfg = SourceConfig(
        id="lo",
        type="lobsters",
        config={"min_score": 20, "tags": ["practices"]},
        topics=["t"],
        weight=0.7,
    )
    src = make_source(cfg)
    assert isinstance(src, LobstersSource)
    assert src.min_score == 20
    assert src.tags == ("practices",)


def test_make_safari_source():
    cfg = SourceConfig(
        id="safari",
        type="safari_reading_list",
        config={"write_back": False},
        topics=["t"],
        weight=1.0,
    )
    src = make_source(cfg)
    assert isinstance(src, SafariReadingListSource)
    assert src.write_back is False


def test_make_rsshub_twitter_list_source():
    cfg = SourceConfig(
        id="x_ai",
        type="rsshub_twitter_list",
        config={"list_id": "9988776655", "rsshub_base_url": "http://rsshub:1200"},
        topics=["topic_a"],
        weight=0.7,
    )
    src = make_source(cfg)
    assert isinstance(src, RssHubTwitterListSource)
    assert src.list_id == "9988776655"
    assert src.rsshub_base_url == "http://rsshub:1200"


def test_unknown_source_type():
    cfg = SourceConfig(id="x", type="not_a_real_type", config={}, topics=[], weight=1.0)
    with pytest.raises(ValueError):
        make_source(cfg)
