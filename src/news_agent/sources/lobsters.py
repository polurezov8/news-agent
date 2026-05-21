"""Lobste.rs adapter — JSON API at https://lobste.rs/hottest.json."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from news_agent.config.schema import SourceConfig
from news_agent.core.identity import content_hash, make_article_id
from news_agent.core.types import Article, SourceId, TopicId

from .base import register_source

LOBSTERS_HOTTEST = "https://lobste.rs/hottest.json"


def parse_stories(source_id: SourceId, items: list[dict]) -> list[Article]:
    """Pure parse step — testable without network."""
    out: list[Article] = []
    for item in items:
        title = (item.get("title") or "").strip()
        url = (item.get("url") or item.get("comments_url") or "").strip()
        if not title or not url:
            continue
        body = item.get("description") or ""
        created = item.get("created_at")
        published = (
            datetime.fromisoformat(created.replace("Z", "+00:00"))
            if isinstance(created, str)
            else datetime.now(timezone.utc)
        )
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
                raw={"lobsters": item},
            )
        )
    return out


@register_source("lobsters")
@dataclass(frozen=True, slots=True)
class LobstersSource:
    id: SourceId
    topics: list[TopicId]
    weight: float
    min_score: int = 0
    tags: tuple[str, ...] = ()
    timeout_s: float = 15.0

    def _filter(self, items: list[dict]) -> list[dict]:
        kept: list[dict] = []
        for item in items:
            if (item.get("score") or 0) < self.min_score:
                continue
            if self.tags:
                story_tags = set(item.get("tags") or [])
                if not story_tags.intersection(self.tags):
                    continue
            kept.append(item)
        return kept

    def fetch(self) -> list[Article]:
        import httpx

        with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
            resp = client.get(
                LOBSTERS_HOTTEST,
                headers={"User-Agent": "news-agent/0.1"},
            )
            resp.raise_for_status()
            items = resp.json() or []
        return parse_stories(self.id, self._filter(items))

    @classmethod
    def from_config(cls, cfg: SourceConfig) -> "LobstersSource":
        return cls(
            id=SourceId(cfg.id),
            topics=[TopicId(t) for t in cfg.topics],
            weight=cfg.weight,
            min_score=int(cfg.config.get("min_score", 0)),
            tags=tuple(cfg.config.get("tags") or ()),
            timeout_s=float(cfg.config.get("timeout_s", 15.0)),
        )
