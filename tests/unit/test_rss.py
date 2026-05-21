from __future__ import annotations

from pathlib import Path

from news_agent.core.types import SourceId
from news_agent.sources.rss import parse_feed

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample.rss.xml"


def test_parse_feed_extracts_articles():
    articles = parse_feed(SourceId("test-feed"), FIXTURE.read_bytes())
    assert len(articles) == 2
    assert articles[0].title == "First post about a thing"
    assert articles[0].url == "https://example.com/post-1"
    assert articles[0].source == "test-feed"
    assert articles[0].id.startswith("test-feed:")
    assert len(articles[0].content_hash) == 16


def test_parse_feed_deterministic_id():
    a1 = parse_feed(SourceId("s"), FIXTURE.read_bytes())[0]
    a2 = parse_feed(SourceId("s"), FIXTURE.read_bytes())[0]
    assert a1.id == a2.id
    assert a1.content_hash == a2.content_hash
