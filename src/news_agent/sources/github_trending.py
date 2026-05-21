"""GitHub Trending adapter — scrapes https://github.com/trending HTML.

No official API. HTML structure is stable: each repo is an <article class="Box-row">
with an <h2><a href="/owner/repo">, a <p> description, and a span with "X stars today".
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from news_agent.config.schema import SourceConfig
from news_agent.core.identity import content_hash, make_article_id
from news_agent.core.types import Article, SourceId, TopicId

from .base import register_source

GH_TRENDING_BASE = "https://github.com/trending"

_REPO_BLOCK_RE = re.compile(
    r'<article class="Box-row">(.*?)</article>', re.DOTALL
)
_REPO_PATH_RE = re.compile(
    r'<h2[^>]*>\s*<a[^>]*href="/([^"/]+/[^"]+)"', re.DOTALL
)
_DESCRIPTION_RE = re.compile(
    r'<p[^>]*col-9[^>]*>(.*?)</p>', re.DOTALL
)
_STARS_PERIOD_RE = re.compile(r'(\d[\d,]*)\s+stars?\s+(?:today|this\s+week|this\s+month)')


def parse_trending_html(source_id: SourceId, raw: str) -> list[tuple[Article, int]]:
    """Pure parse step. Returns (article, stars_today) tuples for downstream filtering."""
    out: list[tuple[Article, int]] = []
    for block in _REPO_BLOCK_RE.findall(raw):
        path_m = _REPO_PATH_RE.search(block)
        if not path_m:
            continue
        repo_path = path_m.group(1).strip().replace("\n", "").replace(" ", "")
        url = f"https://github.com/{repo_path}"
        title = repo_path

        desc_m = _DESCRIPTION_RE.search(block)
        body = ""
        if desc_m:
            body = html.unescape(re.sub(r"<[^>]+>", "", desc_m.group(1))).strip()

        stars_today = 0
        stars_m = _STARS_PERIOD_RE.search(block)
        if stars_m:
            stars_today = int(stars_m.group(1).replace(",", ""))

        ch = content_hash(title, body)
        article = Article(
            id=make_article_id(source_id, ch),
            source=source_id,
            url=url,
            title=title,
            body=body,
            content_hash=ch,
            published_at=datetime.now(timezone.utc),
            fetched_at=datetime.now(timezone.utc),
            raw={"github_trending": {"stars_today": stars_today}},
        )
        out.append((article, stars_today))
    return out


def build_url(language: str | None, since: str) -> str:
    from urllib.parse import quote

    path = f"{GH_TRENDING_BASE}/{quote(language, safe='')}" if language else GH_TRENDING_BASE
    return f"{path}?since={since}"


@register_source("github_trending")
@dataclass(frozen=True, slots=True)
class GitHubTrendingSource:
    id: SourceId
    topics: list[TopicId]
    weight: float
    language: str | None = None
    since: str = "daily"                     # daily | weekly | monthly
    min_stars_today: int = 0
    timeout_s: float = 15.0

    def fetch(self) -> list[Article]:
        import httpx

        with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
            resp = client.get(
                build_url(self.language, self.since),
                headers={"User-Agent": "news-agent/0.1"},
            )
            resp.raise_for_status()
            pairs = parse_trending_html(self.id, resp.text)
            return [a for a, stars in pairs if stars >= self.min_stars_today]

    @classmethod
    def from_config(cls, cfg: SourceConfig) -> "GitHubTrendingSource":
        return cls(
            id=SourceId(cfg.id),
            topics=[TopicId(t) for t in cfg.topics],
            weight=cfg.weight,
            language=cfg.config.get("language"),
            since=cfg.config.get("since", "daily"),
            min_stars_today=int(cfg.config.get("min_stars_today", 0)),
            timeout_s=float(cfg.config.get("timeout_s", 15.0)),
        )
