"""arXiv export API adapter (returns Atom XML; reuses RSS parser).

Endpoint: http://export.arxiv.org/api/query?search_query=cat:cs.AI+OR+cat:cs.LG
         &sortBy=submittedDate&sortOrder=descending&max_results=N
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import struct_time
from urllib.parse import urlencode

import feedparser

from news_agent.config.schema import SourceConfig
from news_agent.core.identity import content_hash, make_article_id
from news_agent.core.types import Article, SourceId, TopicId

from .base import register_source

ARXIV_BASE = "http://export.arxiv.org/api/query"


def _struct_to_dt(s: struct_time | None) -> datetime:
    if s is None:
        return datetime.now(timezone.utc)
    return datetime(*s[:6], tzinfo=timezone.utc)


def build_url(categories: list[str], max_results: int) -> str:
    query = " OR ".join(f"cat:{c}" for c in categories)
    return f"{ARXIV_BASE}?" + urlencode(
        {
            "search_query": query,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": max_results,
        }
    )


def parse_atom(source_id: SourceId, raw: str | bytes) -> list[Article]:
    """Pure parse step — testable without network."""
    parsed = feedparser.parse(raw)
    out: list[Article] = []
    for entry in parsed.entries:
        title = entry.get("title", "").replace("\n", " ").strip()
        url = entry.get("link", "").strip()
        body = entry.get("summary", "").strip()
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
                raw={"arxiv": dict(entry)},
            )
        )
    return out


@register_source("arxiv")
@dataclass(frozen=True, slots=True)
class ArxivSource:
    id: SourceId
    topics: list[TopicId]
    weight: float
    categories: tuple[str, ...]
    max_results: int = 50
    timeout_s: float = 20.0

    def fetch(self) -> list[Article]:
        import httpx

        with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
            resp = client.get(build_url(list(self.categories), self.max_results))
            resp.raise_for_status()
            return parse_atom(self.id, resp.content)

    @classmethod
    def from_config(cls, cfg: SourceConfig) -> "ArxivSource":
        cats = cfg.config.get("categories") or []
        if not cats:
            raise ValueError(f"arxiv source {cfg.id!r} missing config.categories")
        return cls(
            id=SourceId(cfg.id),
            topics=[TopicId(t) for t in cfg.topics],
            weight=cfg.weight,
            categories=tuple(cats),
            max_results=int(cfg.config.get("max_results", 50)),
            timeout_s=float(cfg.config.get("timeout_s", 20.0)),
        )
