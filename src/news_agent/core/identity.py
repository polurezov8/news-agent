"""Deterministic content identity helpers.

ArticleId is derived from (source, content_hash) so re-fetches produce the same
id, enabling idempotent inserts and stable cross-source dedup by content_hash.
"""

from __future__ import annotations

import hashlib
import re

from .types import ArticleId, ContentHash, SourceId

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Lowercase, strip, collapse whitespace. Stable across formatting noise."""
    return _WHITESPACE_RE.sub(" ", text.strip().lower())


def content_hash(title: str, body: str) -> ContentHash:
    """16-char sha256 of normalized title + body. Cross-source dedup key."""
    h = hashlib.sha256()
    h.update(_normalize(title).encode("utf-8"))
    h.update(b"\x00")
    h.update(_normalize(body).encode("utf-8"))
    return ContentHash(h.hexdigest()[:16])


def make_article_id(source: SourceId, ch: ContentHash) -> ArticleId:
    return ArticleId(f"{source}:{ch}")
