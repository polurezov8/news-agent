from __future__ import annotations

from pathlib import Path

import pytest

from news_agent.config.schema import SourceConfig
from news_agent.core.types import SourceId
from news_agent.sources.rss import parse_feed
from news_agent.sources.rsshub_twitter_list import RssHubTwitterListSource

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample.rss.xml"


def test_feed_url_construction():
    src = RssHubTwitterListSource(
        id=SourceId("x_ai"), topics=[], weight=1.0,
        list_id="1234567890", rsshub_base_url="http://rsshub:1200",
    )
    assert src.feed_url == "http://rsshub:1200/twitter/list/1234567890"


def test_feed_url_strips_trailing_slash():
    src = RssHubTwitterListSource(
        id=SourceId("x_ai"), topics=[], weight=1.0,
        list_id="abc", rsshub_base_url="http://rsshub:1200/",
    )
    assert src.feed_url == "http://rsshub:1200/twitter/list/abc"


def test_parse_rss_fixture_produces_articles():
    xml = FIXTURE.read_text()
    articles = parse_feed(SourceId("x_ai"), xml)
    assert len(articles) >= 1
    for a in articles:
        assert a.title
        assert a.url.startswith("http")


def test_from_config_missing_list_id_raises():
    cfg = SourceConfig(id="x", type="rsshub_twitter_list", config={}, topics=[], weight=1.0)
    with pytest.raises(ValueError, match="missing config.list_id"):
        RssHubTwitterListSource.from_config(cfg)


def test_from_config_full():
    cfg = SourceConfig(
        id="x_ai_list",
        type="rsshub_twitter_list",
        config={"list_id": "99887766", "rsshub_base_url": "http://myrsshub:1200"},
        topics=["topic_a"],
        weight=0.8,
    )
    src = RssHubTwitterListSource.from_config(cfg)
    assert isinstance(src, RssHubTwitterListSource)
    assert src.list_id == "99887766"
    assert src.feed_url == "http://myrsshub:1200/twitter/list/99887766"
    assert src.topics[0] == "topic_a"
