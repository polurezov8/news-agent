from __future__ import annotations

import json
from pathlib import Path

from news_agent.core.types import SourceId
from news_agent.sources.lobsters import LobstersSource, parse_stories

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample.lobsters.json"


def _items() -> list[dict]:
    return json.loads(FIXTURE.read_text())


def test_parse_stories_falls_back_to_comments_url():
    arts = parse_stories(SourceId("lo"), _items())
    titles = {a.title for a in arts}
    assert "Missing url uses comments_url" in titles
    by_title = {a.title: a.url for a in arts}
    assert by_title["Missing url uses comments_url"] == "https://lobste.rs/s/ghi789"


def test_filter_min_score():
    src = LobstersSource(
        id=SourceId("lo"), topics=[], weight=1.0, min_score=10, tags=()
    )
    kept = src._filter(_items())
    titles = {it["title"] for it in kept}
    assert "An interesting tech post" in titles
    assert "Low score item" not in titles


def test_filter_tags():
    src = LobstersSource(
        id=SourceId("lo"), topics=[], weight=1.0, min_score=0, tags=("practices",)
    )
    kept = src._filter(_items())
    titles = {it["title"] for it in kept}
    assert titles == {"An interesting tech post"}
