"""Slack Socket Mode listener — slash commands, button actions, reactions → CorrectionEvent."""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from news_agent.core.types import (
    ArticleId,
    CorrectionEvent,
    CorrectionKind,
    SourceId,
    SurfaceRef,
    TopicId,
)
from news_agent.storage.repository import connect, find_surface_targets

_REACTION_MAP: dict[str, CorrectionKind] = {
    "+1": CorrectionKind.BOOST,
    "thumbsup": CorrectionKind.BOOST,
    "-1": CorrectionKind.DEMOTE,
    "thumbsdown": CorrectionKind.DEMOTE,
}

_ACTION_MAP: dict[str, CorrectionKind] = {
    "boost": CorrectionKind.BOOST,
    "save": CorrectionKind.SAVE,
    "skip": CorrectionKind.SKIP,
    "demote": CorrectionKind.DEMOTE,
}

OnCorrection = Callable[[CorrectionEvent], None]


def _resolve_reactions(
    conn: sqlite3.Connection,
    *,
    channel: str,
    message_ts: str,
    kind: CorrectionKind,
    user: str,
) -> list[CorrectionEvent]:
    """Resolve a reaction to one CorrectionEvent per item in the reacted message.

    A digest message can carry multiple items; a 👍/👎 on it is a signal about
    every item it surfaced, not an arbitrary one. Returns [] when the message
    isn't ours.

    The article id encodes the source as its prefix (e.g. "hn:abc…"), so source
    is derived from there rather than another DB hop.
    """
    targets = find_surface_targets(conn, channel, message_ts)
    if not targets:
        return []
    now = datetime.now(timezone.utc)
    surface = SurfaceRef(
        surface="slack",
        channel=channel,
        message_id=message_ts,
        posted_at=now,
    )
    events: list[CorrectionEvent] = []
    for article_id, topic_id in targets:
        aid_str = str(article_id)
        source_id = SourceId(aid_str.split(":", 1)[0]) if ":" in aid_str else SourceId("unknown")
        events.append(CorrectionEvent(
            article=article_id,
            topic=topic_id,
            source=source_id,
            kind=kind,
            surface=surface,
            user=user,
            at=now,
        ))
    return events


def make_app(
    notifier=None,
    on_correction: OnCorrection | None = None,
    *,
    db_path: Path | None = None,
) -> App:
    """Build the Slack Bolt app.

    notifier: SlackNotifier — used by `/news demo`.
    on_correction: called with each CorrectionEvent from buttons or reactions.
    db_path: required for reaction → article lookup (find_surface_target).
    """
    app = App(token=os.environ["SLACK_BOT_TOKEN"])

    @app.command("/news")
    def handle_news(ack, respond, command):
        ack()
        text = (command.get("text") or "").strip().lower()
        if text == "demo":
            if notifier is None:
                respond("Notifier not configured.")
                return
            from news_agent.notifier.slack import _demo_payload

            notifier.post_digest(_demo_payload())
            respond("Demo digest posted to channel ✓")
        elif text == "status":
            from news_agent.application import load_default_context
            from news_agent.llm.costs import cents_to_dollars
            from news_agent.storage.repository import connect, monthly_usd_cents

            ctx = load_default_context()
            conn = connect(ctx.db_path)
            try:
                spent = monthly_usd_cents(conn, now=datetime.now(timezone.utc))
            finally:
                conn.close()
            respond(f"MTD spend: {cents_to_dollars(spent)} of {cents_to_dollars(int(ctx.budget_usd * 100))}")
        else:
            respond("Running the pipeline now…")
            try:
                from news_agent.application import run_pipeline
                from news_agent.core.types import Cadence

                result = run_pipeline(Cadence.DAILY, log=lambda _m: None)
                respond(f"Done — {result.fetched} fetched, {result.scored} scored, {result.posted} posted.")
            except Exception as exc:
                respond(f"Run failed: {exc}")

    @app.action(re.compile(r"^(boost|save|skip|demote)$"))
    def handle_button(ack, body, action, logger):
        ack()
        kind = _ACTION_MAP.get(action["action_id"])
        if kind is None or on_correction is None:
            return

        value = action.get("value", "")
        article_id_raw, _, topic_raw = value.partition("|")
        if not article_id_raw:
            logger.warning("Button action missing article_id in value: %s", value)
            return

        channel_id = (body.get("channel") or {}).get("id", "")
        message_ts = (body.get("message") or {}).get("ts", "")
        surface = SurfaceRef(
            surface="slack",
            channel=channel_id,
            message_id=message_ts,
            posted_at=datetime.now(timezone.utc),
        )
        source_raw = article_id_raw.split(":", 1)[0] if ":" in article_id_raw else "unknown"
        on_correction(CorrectionEvent(
            article=ArticleId(article_id_raw),
            topic=TopicId(topic_raw or "unknown"),
            source=SourceId(source_raw),
            kind=kind,
            surface=surface,
            user=(body.get("user") or {}).get("id", ""),
            at=datetime.now(timezone.utc),
        ))

    @app.event("reaction_added")
    def handle_reaction(body, event, logger):
        # Strip skin-tone modifier (e.g. "thumbsup::skin-tone-3" → "thumbsup").
        reaction_name = (event.get("reaction") or "").split("::")[0]
        kind = _REACTION_MAP.get(reaction_name)
        if kind is None or on_correction is None:
            return
        if db_path is None:
            logger.warning("reaction_added received but listener has no db_path; dropping")
            return
        item = event.get("item") or {}
        conn = connect(db_path)
        try:
            evs = _resolve_reactions(
                conn,
                channel=item.get("channel", ""),
                message_ts=item.get("ts", ""),
                kind=kind,
                user=event.get("user", ""),
            )
        finally:
            conn.close()
        if not evs:
            logger.info(
                "reaction on message %s not in surfaces; ignoring",
                item.get("ts", ""),
            )
            return
        for ev in evs:
            on_correction(ev)

    # Per-DM conversation history (in-memory; reset on listener restart).
    history: dict[str, list] = {}

    @app.event("message")
    def handle_message(event, say, logger):
        # Only real user DMs — skip bot posts, edits, our own digests.
        if event.get("channel_type") != "im":
            return
        if event.get("bot_id") or event.get("subtype"):
            return
        text = (event.get("text") or "").strip()
        if not text:
            return

        from langchain_core.messages import AIMessage, HumanMessage

        from news_agent.notifier.assistant import answer

        channel = event.get("channel", "")
        turns = history.setdefault(channel, [])
        turns.append(HumanMessage(content=text))
        try:
            reply = answer(turns)
        except Exception as exc:
            logger.warning("assistant error: %s", exc)
            say("Something went wrong handling that.")
            return
        turns.append(AIMessage(content=reply))
        del turns[:-12]  # keep the last few exchanges
        say(reply)

    return app


def start(app: App) -> None:
    """Start Socket Mode — blocks until interrupted."""
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
