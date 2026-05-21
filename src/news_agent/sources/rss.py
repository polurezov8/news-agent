"""RSS / Atom feed adapter via feedparser."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import struct_time

import feedparser

from news_agent.config.schema import SourceConfig
from news_agent.core.identity import content_hash, make_article_id
from news_agent.core.types import Article, SourceId, TopicId

from .base import register_source


def _struct_to_dt(s: struct_time | None) -> datetime:
    if s is None:
        return datetime.now(timezone.utc)
    return datetime(*s[:6], tzinfo=timezone.utc)


def parse_feed(source_id: SourceId, raw_xml: str | bytes) -> list[Article]:
    """Pure parse step — testable without network."""
    parsed = feedparser.parse(raw_xml)
    out: list[Article] = []
    for entry in parsed.entries:
        title = entry.get("title", "").strip()
        url = entry.get("link", "").strip()
        body = entry.get("summary", "") or entry.get("description", "") or ""
        if not title or not url:
            continue
        published = _struct_to_dt(entry.get("published_parsed") or entry.get("updated_parsed"))
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
                raw={"entry": dict(entry)},
            )
        )
    return out


@register_source("rss")
@dataclass(frozen=True, slots=True)
class RssSource:
    id: SourceId
    topics: list[TopicId]
    weight: float
    url: str
    timeout_s: float = 15.0

    def fetch(self) -> list[Article]:
        import httpx

        with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
            resp = client.get(self.url, headers={"User-Agent": "news-agent/0.1"})
            resp.raise_for_status()
            return parse_feed(self.id, resp.content)

    @classmethod
    def from_config(cls, cfg: SourceConfig) -> "RssSource":
        url = cfg.config.get("url")
        if not url:
            raise ValueError(f"rss source {cfg.id!r} missing config.url")
        return cls(
            id=SourceId(cfg.id),
            topics=[TopicId(t) for t in cfg.topics],
            weight=cfg.weight,
            url=url,
            timeout_s=float(cfg.config.get("timeout_s", 15.0)),
        )
