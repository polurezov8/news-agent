"""Safari Reading List adapter (macOS, read-only in v1.0).

Reads ~/Library/Safari/Bookmarks.plist. Requires Full Disk Access for the
running process under modern macOS TCC. Write-back lands in M9.
"""

from __future__ import annotations

import plistlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from news_agent.config.schema import SourceConfig
from news_agent.core.identity import content_hash, make_article_id
from news_agent.core.types import Article, SourceId, TopicId

from .base import register_source

DEFAULT_BOOKMARKS_PATH = Path("~/Library/Safari/Bookmarks.plist").expanduser()
READING_LIST_TITLE = "com.apple.ReadingList"


def _find_reading_list_node(node: dict) -> dict | None:
    """Recursive search for the Reading List folder."""
    if node.get("Title") == READING_LIST_TITLE:
        return node
    for child in node.get("Children", []) or []:
        if isinstance(child, dict):
            found = _find_reading_list_node(child)
            if found is not None:
                return found
    return None


def parse_bookmarks_plist(source_id: SourceId, raw: bytes) -> list[Article]:
    """Pure parse step — testable without filesystem access."""
    root = plistlib.loads(raw)
    rl = _find_reading_list_node(root)
    if rl is None:
        return []
    out: list[Article] = []
    for item in rl.get("Children", []) or []:
        url = item.get("URLString", "").strip()
        title = (item.get("URIDictionary", {}) or {}).get("title", "").strip()
        if not url or not title:
            continue
        reading_list = item.get("ReadingList", {}) or {}
        body = reading_list.get("PreviewText", "") or ""
        date_added = reading_list.get("DateAdded")
        published = (
            date_added if isinstance(date_added, datetime)
            else datetime.now(timezone.utc)
        )
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        ch = content_hash(title, body)
        out.append(
            Article(
                id=make_article_id(source_id, ch),
                source=source_id,
                url=url,
                title=title,
                body=body,
                content_hash=ch,
                published_at=published,
                fetched_at=datetime.now(timezone.utc),
                raw={"safari": item},
            )
        )
    return out


@register_source("safari_reading_list")
@dataclass(frozen=True, slots=True)
class SafariReadingListSource:
    id: SourceId
    topics: list[TopicId]
    weight: float
    bookmarks_path: Path = DEFAULT_BOOKMARKS_PATH
    write_back: bool = False                 # v1.0 always reads only; M9 enables

    def fetch(self) -> list[Article]:
        if not self.bookmarks_path.exists():
            return []
        return parse_bookmarks_plist(self.id, self.bookmarks_path.read_bytes())

    @classmethod
    def from_config(cls, cfg: SourceConfig) -> "SafariReadingListSource":
        path_str = cfg.config.get("bookmarks_path")
        path = Path(path_str).expanduser() if path_str else DEFAULT_BOOKMARKS_PATH
        return cls(
            id=SourceId(cfg.id),
            topics=[TopicId(t) for t in cfg.topics],
            weight=cfg.weight,
            bookmarks_path=path,
            write_back=bool(cfg.config.get("write_back", False)),
        )
