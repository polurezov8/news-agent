"""Generic RSSHub adapter. Provide any RSSHub route as `config.route`.

Examples:
  /twitter/user/karpathy
  /telegram/channel/iOSDevsUA
  /twitter/list/123456
  /github/trending/daily/Swift
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from news_agent.config.schema import SourceConfig
from news_agent.core.types import Article, SourceId, TopicId

from .base import register_source
from .rss import parse_feed

_DEFAULT_BASE = "http://localhost:1200"


@register_source("rsshub")
@dataclass(frozen=True, slots=True)
class RssHubSource:
    id: SourceId
    topics: list[TopicId]
    weight: float
    route: str
    rsshub_base_url: str = _DEFAULT_BASE
    timeout_s: float = 20.0

    @property
    def feed_url(self) -> str:
        base = self.rsshub_base_url.rstrip("/")
        route = self.route if self.route.startswith("/") else "/" + self.route
        return f"{base}{route}"

    def fetch(self) -> list[Article]:
        import httpx

        with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
            resp = client.get(self.feed_url, headers={"User-Agent": "news-agent/0.1"})
            resp.raise_for_status()
            return parse_feed(self.id, resp.content)

    @classmethod
    def from_config(cls, cfg: SourceConfig) -> "RssHubSource":
        route = cfg.config.get("route")
        if not route:
            raise ValueError(f"rsshub source {cfg.id!r} missing config.route")
        base = (
            cfg.config.get("rsshub_base_url")
            or os.environ.get("RSSHUB_BASE_URL", _DEFAULT_BASE)
        )
        return cls(
            id=SourceId(cfg.id),
            topics=[TopicId(t) for t in cfg.topics],
            weight=cfg.weight,
            route=str(route),
            rsshub_base_url=base,
            timeout_s=float(cfg.config.get("timeout_s", 20.0)),
        )
