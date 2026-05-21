"""RSSHub Twitter/X List adapter — routes list RSS through self-hosted RSSHub."""

from __future__ import annotations

import os
from dataclasses import dataclass

from news_agent.config.schema import SourceConfig
from news_agent.core.types import Article, SourceId, TopicId

from .base import register_source
from .rss import parse_feed

_DEFAULT_BASE = "http://localhost:1200"


@register_source("rsshub_twitter_list")
@dataclass(frozen=True, slots=True)
class RssHubTwitterListSource:
    id: SourceId
    topics: list[TopicId]
    weight: float
    list_id: str
    rsshub_base_url: str = _DEFAULT_BASE
    timeout_s: float = 15.0

    @property
    def feed_url(self) -> str:
        base = self.rsshub_base_url.rstrip("/")
        return f"{base}/twitter/list/{self.list_id}"

    def fetch(self) -> list[Article]:
        import httpx

        with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
            resp = client.get(self.feed_url, headers={"User-Agent": "news-agent/0.1"})
            resp.raise_for_status()
            return parse_feed(self.id, resp.content)

    @classmethod
    def from_config(cls, cfg: SourceConfig) -> "RssHubTwitterListSource":
        list_id = cfg.config.get("list_id")
        if not list_id:
            raise ValueError(f"rsshub_twitter_list source {cfg.id!r} missing config.list_id")
        base = (
            cfg.config.get("rsshub_base_url")
            or os.environ.get("RSSHUB_BASE_URL", _DEFAULT_BASE)
        )
        return cls(
            id=SourceId(cfg.id),
            topics=[TopicId(t) for t in cfg.topics],
            weight=cfg.weight,
            list_id=str(list_id),
            rsshub_base_url=base,
            timeout_s=float(cfg.config.get("timeout_s", 15.0)),
        )
