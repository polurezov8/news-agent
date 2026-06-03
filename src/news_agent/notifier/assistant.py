"""Conversational assistant for the Slack DM.

A Sonnet tool-use loop over read-only corpus queries plus two side-effecting
actions (run the pipeline now, nudge a source prior). Both side-effects are
explicit user actions; the system prompt tells the model to confirm before
either. The model reads through tools; it never writes SQL directly.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SYSTEM = """You are the assistant for a personal news agent, talking to your owner in a Slack DM.

You can:
- search the collected articles (search_news)
- show what was recently surfaced (recent_picks)
- report what the agent has learned about their taste (my_taste)
- report spend and counts (cost_status)
- run the pipeline immediately (run_now) — this costs a little money and posts a digest
- nudge how much a source is trusted for its topics (adjust_source)

Be terse and concrete. Use the tools rather than guessing. For run_now and
adjust_source: restate exactly what you'll do and ask for a yes BEFORE calling
the tool. Never call those two without a clear confirmation in the conversation.
Link articles as Slack links: <url|title>."""

_MODEL_ENV = "NEWS_AGENT_MODEL_SMART"
_DEFAULT_MODEL = "claude-sonnet-4-6"
_MAX_ROUNDS = 6


def _db_path() -> Path:
    return Path(os.environ.get("NEWS_AGENT_DB", "./news_agent.db"))


def _fmt_articles(rows) -> str:
    if not rows:
        return "No matches."
    out = []
    seen: set[str] = set()  # same item is re-fetched over time; show each URL once
    for article, best in rows:
        if article.url in seen:
            continue
        seen.add(article.url)
        score = f" ({best:.2f})" if best is not None else ""
        out.append(f"• <{article.url}|{article.title}> — {article.source}{score}")
    return "\n".join(out)


def build_tools():
    """Build the langchain tools, each closing over the DB path / env."""
    from langchain_core.tools import tool

    from news_agent.storage.repository import (
        connect,
        load_taste,
        monthly_usd_cents,
        query_top_scored,
        search_articles,
    )

    @tool
    def search_news(query: str, topic: str = "", days: int = 30) -> str:
        """Search collected articles by keyword in title/body. Optional topic
        filter (ai|ios|eng_leadership) and lookback window in days."""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        conn = connect(_db_path())
        try:
            rows = search_articles(
                conn, query=query, topic=topic or None, since=since, limit=10
            )
        finally:
            conn.close()
        return _fmt_articles(rows)

    @tool
    def recent_picks(days: int = 7) -> str:
        """The highest-scored articles from the last N days."""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        conn = connect(_db_path())
        try:
            rows = query_top_scored(conn, since=since, limit=10)
        finally:
            conn.close()
        return _fmt_articles([(a, s.final) for a, s in rows])

    @tool
    def my_taste() -> str:
        """What the agent has learned you're interested in, from your reading list."""
        from news_agent.learning.taste import top_taste

        conn = connect(_db_path())
        try:
            weights = load_taste(conn)
        finally:
            conn.close()
        if not weights:
            return "No taste profile yet — it builds from your Safari reading list."
        return ", ".join(f"{t} ({w:.2f})" for t, w in top_taste(weights, n=12))

    @tool
    def cost_status() -> str:
        """Month-to-date LLM spend."""
        conn = connect(_db_path())
        try:
            cents = monthly_usd_cents(conn, now=datetime.now(timezone.utc))
        finally:
            conn.close()
        return f"MTD spend: ${cents / 100:.2f}"

    @tool
    def run_now() -> str:
        """Run the daily pipeline immediately. Costs money and posts a digest.
        Only call after the user confirms."""
        from news_agent.application import run_pipeline
        from news_agent.core.types import Cadence

        result = run_pipeline(Cadence.DAILY, log=lambda _m: None)
        return (
            f"Done — {result.fetched} fetched, {result.scored} scored, "
            f"{result.posted} posted."
        )

    @tool
    def adjust_source(source: str, direction: str, topic: str = "") -> str:
        """Nudge how much a source is trusted. direction is 'boost' or 'demote'.
        Omit topic to apply across all the source's topics. Only call after the
        user confirms."""
        from news_agent.application import adjust_source_prior

        changes = adjust_source_prior(
            source, direction, topic=topic or None, log=lambda _m: None
        )
        return "Updated: " + ", ".join(f"{t} {b:.2f}→{a:.2f}" for t, b, a in changes)

    return [search_news, recent_picks, my_taste, cost_status, run_now, adjust_source]


def answer(history: list, *, model: str | None = None) -> str:
    """Run the tool-use loop over a thread's message history; return reply text.

    `history` is a list of langchain Human/AI messages (the conversation so far,
    newest last). Returns the assistant's final text."""
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import SystemMessage, ToolMessage

    tools = build_tools()
    tool_map = {t.name: t for t in tools}
    llm = ChatAnthropic(
        model=model or os.environ.get(_MODEL_ENV, _DEFAULT_MODEL),
        max_tokens=1024,
    ).bind_tools(tools)

    messages = [SystemMessage(content=_SYSTEM), *history]
    for _ in range(_MAX_ROUNDS):
        resp = llm.invoke(messages)
        messages.append(resp)
        if not resp.tool_calls:
            return _text_of(resp)
        for call in resp.tool_calls:
            tool = tool_map.get(call["name"])
            try:
                result = tool.invoke(call["args"]) if tool else f"Unknown tool {call['name']}"
            except Exception as exc:
                result = f"Tool error: {exc}"
            messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))
    return "Sorry — I got stuck working that out. Try rephrasing?"


def _text_of(resp) -> str:
    content = resp.content
    if isinstance(content, str):
        return content
    # Anthropic may return a list of content blocks; keep the text parts.
    parts = [b.get("text", "") for b in content if isinstance(b, dict)]
    return "".join(parts).strip() or "(no reply)"
