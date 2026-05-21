"""Sonnet substance scorer. Batches by topic (one LLM call per topic per run)."""

from __future__ import annotations

import json
import re

from news_agent.config.schema import TopicConfig
from news_agent.core.types import Article, Tag

BATCH_SIZE = 12
_BODY_TRUNC = 600

_SINGLE_PROMPT = """Rate the substance and relevance of this article for the topic "{label}".

Topic rules:
{rules}

Article tags: {tags}
Title: {title}
Body: {body}

Respond with only a decimal number from 0.0 to 1.0 (higher = more relevant + substantive)."""

_BATCH_PROMPT = """Rate each article's substance and relevance for the topic "{label}".

Topic rules:
{rules}

Articles (numbered):
{articles}

Return ONLY a JSON object mapping each article number (as string) to a decimal score 0.0–1.0. Example:
{{"0": 0.82, "1": 0.41, "2": 0.05}}

Higher = more relevant + substantive. Be discriminating."""


def parse_score(text: str) -> float:
    """Single-article fallback parse."""
    m = re.search(r"(0?\.\d+|1\.0|1|0)", text)
    if not m:
        return 0.5
    try:
        val = float(m.group())
    except ValueError:
        return 0.5
    return max(0.0, min(1.0, val))


def parse_batch_scores(text: str, batch_size: int) -> list[float]:
    """Extract {n: float} JSON. Missing entries default to 0.5."""
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return [0.5] * batch_size
    try:
        raw = json.loads(m.group())
    except json.JSONDecodeError:
        return [0.5] * batch_size
    if not isinstance(raw, dict):
        return [0.5] * batch_size

    out: list[float] = []
    for i in range(batch_size):
        v = raw.get(str(i), 0.5)
        try:
            f = float(v)
        except (TypeError, ValueError):
            f = 0.5
        out.append(max(0.0, min(1.0, f)))
    return out


def _client(model: str):
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=model, max_tokens=256)


def score_substance(
    article: Article,
    tags: frozenset[Tag],
    topic: TopicConfig,
    *,
    model: str = "claude-sonnet-4-6",
    client=None,
) -> float:
    """Single-article path. Tests + small batches; pipeline uses score_batch_for_topic."""
    from langchain_core.messages import HumanMessage

    rules = "\n".join(f"- {r}" for r in topic.nl_rules) or "- (no specific rules)"
    prompt = _SINGLE_PROMPT.format(
        label=topic.label,
        rules=rules,
        tags=", ".join(sorted(str(t) for t in tags)) or "(none)",
        title=article.title,
        body=article.body[:_BODY_TRUNC],
    )
    c = client or _client(model)
    resp = c.invoke([HumanMessage(content=prompt)])
    return parse_score(str(resp.content))


def score_batch_for_topic(
    articles_tags: list[tuple[Article, frozenset[Tag]]],
    topic: TopicConfig,
    *,
    model: str = "claude-sonnet-4-6",
    client=None,
    batch_size: int = BATCH_SIZE,
) -> list[float]:
    """Score N (article, tags) pairs against one topic in batched LLM calls.

    Returns a list of scores aligned with input order.
    """
    from langchain_core.messages import HumanMessage

    if not articles_tags:
        return []

    rules = "\n".join(f"- {r}" for r in topic.nl_rules) or "- (no specific rules)"
    c = client or _client(model)
    out: list[float] = []

    for start in range(0, len(articles_tags), batch_size):
        batch = articles_tags[start:start + batch_size]
        formatted = "\n\n".join(
            f"#{i}\nTags: {', '.join(sorted(str(t) for t in tags)) or '(none)'}\n"
            f"Title: {article.title}\nBody: {article.body[:_BODY_TRUNC]}"
            for i, (article, tags) in enumerate(batch)
        )
        prompt = _BATCH_PROMPT.format(label=topic.label, rules=rules, articles=formatted)
        resp = c.invoke([HumanMessage(content=prompt)])
        out.extend(parse_batch_scores(str(resp.content), len(batch)))

    return out
