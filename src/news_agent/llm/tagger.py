"""Haiku tagger. Batches up to BATCH_SIZE articles per LLM call."""

from __future__ import annotations

import json
import re
from typing import Iterable

from news_agent.config.schema import TagsConfig
from news_agent.core.types import Article, Tag, TagResult

BATCH_SIZE = 10
_BODY_TRUNC = 300

_PROMPT = """Tag each article using only the provided vocabulary.

Vocabulary: [{vocab}]

Articles (numbered):
{articles}

Return ONLY a JSON object mapping each article number (as string) to its tag list. Example:
{{"0": ["essay", "hands_on"], "1": [], "2": ["news"]}}
"""


def _flat_vocab(cfg: TagsConfig) -> list[str]:
    return [t for tags in cfg.tags.values() for t in tags]


def parse_tags(text: str, valid: set[str]) -> list[str]:
    """Single-article fallback — extract first JSON array, filter to valid tags."""
    m = re.search(r"\[.*?\]", text, re.DOTALL)
    if not m:
        return []
    try:
        raw = json.loads(m.group())
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    return [t for t in raw if isinstance(t, str) and t in valid]


def parse_batch(text: str, valid: set[str], batch_size: int) -> list[list[str]]:
    """Extract JSON object {n: [tags]}. Returns list of tag lists, indexed 0..batch_size-1."""
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return [[] for _ in range(batch_size)]
    try:
        raw = json.loads(m.group())
    except json.JSONDecodeError:
        return [[] for _ in range(batch_size)]
    if not isinstance(raw, dict):
        return [[] for _ in range(batch_size)]

    out: list[list[str]] = []
    for i in range(batch_size):
        tags = raw.get(str(i), [])
        if not isinstance(tags, list):
            tags = []
        out.append([t for t in tags if isinstance(t, str) and t in valid])
    return out


def _client(model: str):
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=model, max_tokens=512)


def _tag_batch(
    batch: list[Article],
    vocab: list[str],
    valid: set[str],
    *,
    model: str,
    client,
) -> list[TagResult]:
    from langchain_core.messages import HumanMessage

    formatted = "\n".join(
        f"#{i}\nTitle: {a.title}\nBody: {a.body[:_BODY_TRUNC]}"
        for i, a in enumerate(batch)
    )
    prompt = _PROMPT.format(vocab=", ".join(vocab), articles=formatted)
    resp = client.invoke([HumanMessage(content=prompt)])
    tag_lists = parse_batch(str(resp.content), valid, len(batch))
    return [
        TagResult(
            article=article.id,
            tags=frozenset(Tag(t) for t in tags),
            confidence=1.0,
            model=model,
        )
        for article, tags in zip(batch, tag_lists)
    ]


def tag_article(
    article: Article,
    tags_cfg: TagsConfig,
    *,
    model: str = "claude-haiku-4-5-20251001",
    client=None,
) -> TagResult:
    """Single-article path. Kept for tests/backward compatibility; pipeline uses tag_articles."""
    vocab = _flat_vocab(tags_cfg)
    valid = set(vocab)
    c = client or _client(model)
    results = _tag_batch([article], vocab, valid, model=model, client=c)
    return results[0]


def tag_articles(
    articles: Iterable[Article],
    tags_cfg: TagsConfig,
    *,
    model: str = "claude-haiku-4-5-20251001",
    client=None,
    batch_size: int = BATCH_SIZE,
) -> list[TagResult]:
    """Tag a sequence in batches of `batch_size`. One LLM call per batch."""
    items = list(articles)
    if not items:
        return []

    vocab = _flat_vocab(tags_cfg)
    valid = set(vocab)
    c = client or _client(model)

    out: list[TagResult] = []
    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        out.extend(_tag_batch(batch, vocab, valid, model=model, client=c))
    return out
