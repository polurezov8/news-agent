"""Conversational assistant for the Slack DM.

A Sonnet tool-use loop over read-only corpus queries plus two side-effecting
actions (run the pipeline now, nudge a source prior). Both side-effects are
explicit user actions; the system prompt tells the model to confirm before
either. The model reads through tools; it never writes SQL directly.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SYSTEM = """You are the assistant for a personal news agent, talking to your owner in a Slack DM.

You can:
- search the collected articles (search_news)
- show what was recently surfaced (recent_picks)
- list recent Safari reading-list items the agent ingested (reading_list)
- report what the agent has learned about their taste (my_taste)
- report spend and counts (cost_status)
- run the pipeline immediately (run_now) — this costs a little money and posts a digest
- nudge how much a source is trusted for its topics (adjust_source)

Be terse and concrete. Use the tools rather than guessing. For run_now and
adjust_source: restate exactly what you'll do and ask for a yes BEFORE calling
the tool. Never call those two without a clear confirmation in the conversation.

When run_now reports 0 new / 0 scored / 0 posted, that is NORMAL — it means no
new on-topic articles since the last run (the pipeline dedupes what it has
already seen, and the digest only routes each run's new finds). It is NOT an
error or an API/quota problem. The LLM provider is Anthropic; never say OpenAI.
Do not speculate about failures unless a tool actually returns an error.

Format for Slack mrkdwn, NOT GitHub markdown: bold is *single asterisks*,
italic is _underscores_. Do NOT use **double asterisks** or # headers — Slack
renders those literally. Link articles as <url|title>."""

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
    def reading_list(n: int = 5) -> str:
        """The most recent items from the user's Safari reading list that the
        agent has ingested (read or saved). Use this for 'what's in my reading
        list' / 'my last N saved articles'."""
        from news_agent.config.loader import load_sources
        from news_agent.storage.repository import recent_reading_list

        interest_ids = [
            s.id for s in load_sources().sources
            if getattr(s, "role", "discovery") == "interest"
        ]
        conn = connect(_db_path())
        try:
            rows = recent_reading_list(conn, source_ids=interest_ids, limit=n)
        finally:
            conn.close()
        if not rows:
            return "Nothing from the reading list yet."
        lines = []
        for article, read_at in rows:
            state = "read" if read_at else "saved"
            lines.append(f"• <{article.url}|{article.title}> — {state}")
        return "\n".join(lines)

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
        msg = (
            f"Done — {result.fetched} fetched, {result.new} new, "
            f"{result.scored} scored, {result.posted} posted."
        )
        if result.scored == 0:
            msg += (
                "\n(0 scored just means nothing new on-topic since the last run — "
                "the digest only routes each run's new finds, so repeated manual "
                "runs dedupe to empty. Not an error.)"
            )
        isum = result.interest
        if isum is not None:
            if isum.synced == 0:
                msg += (
                    "\n⚠️ Reading-list sync read 0 items — the daemon likely lacks "
                    "Full Disk Access, so taste isn't updating."
                )
            else:
                msg += (
                    f"\nReading list: {isum.synced} synced, {isum.newly_read} newly "
                    f"read, {isum.taste_tags} taste tags."
                )
        return msg

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

    return [search_news, recent_picks, reading_list, my_taste, cost_status, run_now, adjust_source]


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
            return _to_slack_mrkdwn(_text_of(resp))
        for call in resp.tool_calls:
            tool = tool_map.get(call["name"])
            try:
                result = tool.invoke(call["args"]) if tool else f"Unknown tool {call['name']}"
            except Exception as exc:
                result = f"Tool error: {exc}"
            messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))
    return "Sorry — I got stuck working that out. Try rephrasing?"


def _to_slack_mrkdwn(text: str) -> str:
    """Convert the GitHub-flavored markdown the model tends to emit into Slack
    mrkdwn: `**bold**` -> `*bold*`, `### header` -> `*header*`. Slack renders the
    GitHub forms literally, so the bold never showed."""
    text = re.sub(r"^\s{0,3}#{1,6}\s+(.*?)\s*$", r"*\1*", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text, flags=re.DOTALL)
    return text


def _text_of(resp) -> str:
    content = resp.content
    if isinstance(content, str):
        return content
    # Anthropic may return a list of content blocks; keep the text parts.
    parts = [b.get("text", "") for b in content if isinstance(b, dict)]
    return "".join(parts).strip() or "(no reply)"
