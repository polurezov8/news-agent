"""Hacker News Firebase API adapter.

Endpoints:
  - https://hacker-news.firebaseio.com/v0/topstories.json
  - https://hacker-news.firebaseio.com/v0/item/{id}.json
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from news_agent.config.schema import SourceConfig
from news_agent.core.identity import content_hash, make_article_id
from news_agent.core.types import Article, SourceId, TopicId

from .base import register_source

HN_BASE = "https://hacker-news.firebaseio.com/v0"


class HnFetcher(Protocol):
    def top_story_ids(self) -> list[int]: ...
    def item(self, item_id: int) -> dict: ...


def parse_item(source_id: SourceId, item: dict) -> Article | None:
    """Pure parse step — testable without network."""
    if item.get("type") != "story" or item.get("dead") or item.get("deleted"):
        return None
    title = (item.get("title") or "").strip()
    url = item.get("url") or f"https://news.ycombinator.com/item?id={item['id']}"
    if not title:
        return None
    body = item.get("text") or ""
    published = datetime.fromtimestamp(item.get("time", 0), tz=timezone.utc)
    ch = content_hash(title, body)
    return Article(
        id=make_article_id(source_id, ch),
        source=source_id,
        url=url,
        title=title,
        body=body,
        content_hash=ch,
        published_at=published,
        fetched_at=datetime.now(timezone.utc),
        raw={"hn": item},
    )


def collect_articles(
    source_id: SourceId,
    fetcher: HnFetcher,
    min_points: int,
    limit: int,
) -> list[Article]:
    out: list[Article] = []
    for sid in fetcher.top_story_ids()[:limit]:
        item = fetcher.item(sid)
        if (item.get("score") or 0) < min_points:
            continue
        art = parse_item(source_id, item)
        if art is not None:
            out.append(art)
    return out


class _HttpxFetcher:
    def __init__(self, timeout_s: float):
        import httpx

        self._client = httpx.Client(timeout=timeout_s, follow_redirects=True)

    def top_story_ids(self) -> list[int]:
        resp = self._client.get(f"{HN_BASE}/topstories.json")
        resp.raise_for_status()
        return resp.json()

    def item(self, item_id: int) -> dict:
        resp = self._client.get(f"{HN_BASE}/item/{item_id}.json")
        resp.raise_for_status()
        return resp.json() or {}

    def __del__(self):
        try:
            self._client.close()
        except Exception:
            pass


@register_source("hackernews")
@dataclass(frozen=True, slots=True)
class HackerNewsSource:
    id: SourceId
    topics: list[TopicId]
    weight: float
    min_points: int = 50
    limit: int = 100
    timeout_s: float = 15.0

    def fetch(self) -> list[Article]:
        return collect_articles(
            self.id,
            _HttpxFetcher(self.timeout_s),
            self.min_points,
            self.limit,
        )

    @classmethod
    def from_config(cls, cfg: SourceConfig) -> "HackerNewsSource":
        return cls(
            id=SourceId(cfg.id),
            topics=[TopicId(t) for t in cfg.topics],
            weight=cfg.weight,
            min_points=int(cfg.config.get("min_points", 50)),
            limit=int(cfg.config.get("limit", 100)),
            timeout_s=float(cfg.config.get("timeout_s", 15.0)),
        )
