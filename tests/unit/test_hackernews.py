from __future__ import annotations

from news_agent.core.types import SourceId
from news_agent.sources.hackernews import collect_articles, parse_item


class FakeFetcher:
    def __init__(self, items: dict[int, dict]):
        self._items = items

    def top_story_ids(self) -> list[int]:
        return list(self._items.keys())

    def item(self, item_id: int) -> dict:
        return self._items[item_id]


def test_parse_item_story():
    art = parse_item(
        SourceId("hn"),
        {"id": 1, "type": "story", "title": "Hello", "url": "https://x", "score": 100, "time": 1716286800},
    )
    assert art is not None
    assert art.title == "Hello"
    assert art.url == "https://x"
    assert art.source == "hn"


def test_parse_item_skips_dead_and_non_story():
    assert parse_item(SourceId("hn"), {"id": 1, "type": "comment"}) is None
    assert parse_item(SourceId("hn"), {"id": 2, "type": "story", "title": "x", "dead": True}) is None
    assert parse_item(SourceId("hn"), {"id": 3, "type": "story", "title": "x", "deleted": True}) is None
    assert parse_item(SourceId("hn"), {"id": 4, "type": "story", "title": ""}) is None


def test_collect_articles_filters_min_points():
    items = {
        1: {"id": 1, "type": "story", "title": "low", "score": 10, "time": 0},
        2: {"id": 2, "type": "story", "title": "high", "score": 200, "time": 0},
        3: {"id": 3, "type": "story", "title": "mid", "score": 60, "time": 0},
    }
    arts = collect_articles(SourceId("hn"), FakeFetcher(items), min_points=50, limit=10)
    titles = {a.title for a in arts}
    assert titles == {"high", "mid"}
