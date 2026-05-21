from __future__ import annotations

import plistlib
from datetime import datetime, timezone

from news_agent.core.types import SourceId
from news_agent.sources.safari_reading_list import parse_bookmarks_plist


def _make_plist_bytes() -> bytes:
    plist = {
        "Children": [
            {
                "Title": "Bookmarks Bar",
                "Children": [{"URLString": "https://other.example", "URIDictionary": {"title": "x"}}],
            },
            {
                "Title": "com.apple.ReadingList",
                "Children": [
                    {
                        "URLString": "https://example.com/post-1",
                        "URIDictionary": {"title": "Saved Article One"},
                        "ReadingList": {
                            "PreviewText": "Preview of the saved article",
                            "DateAdded": datetime(2026, 5, 21, 9, 0, tzinfo=timezone.utc),
                        },
                    },
                    {
                        "URLString": "https://example.com/post-2",
                        "URIDictionary": {"title": "Saved Article Two"},
                        "ReadingList": {"PreviewText": ""},
                    },
                    {
                        # Missing URL — should be skipped
                        "URIDictionary": {"title": "No URL"},
                        "ReadingList": {},
                    },
                ],
            },
        ]
    }
    return plistlib.dumps(plist)


def test_parse_bookmarks_extracts_reading_list_only():
    arts = parse_bookmarks_plist(SourceId("safari"), _make_plist_bytes())
    titles = {a.title for a in arts}
    assert titles == {"Saved Article One", "Saved Article Two"}
    assert all(a.source == "safari" for a in arts)


def test_parse_bookmarks_no_reading_list_node():
    raw = plistlib.dumps({"Children": [{"Title": "Bookmarks Bar"}]})
    assert parse_bookmarks_plist(SourceId("safari"), raw) == []
