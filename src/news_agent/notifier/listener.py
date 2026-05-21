"""Slack Socket Mode listener — slash commands, button actions, reactions → CorrectionEvent."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
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

_REACTION_MAP: dict[str, CorrectionKind] = {
    "+1": CorrectionKind.BOOST,
    "thumbsup": CorrectionKind.BOOST,
    "bookmark": CorrectionKind.SAVE,
    "zzz": CorrectionKind.SKIP,
    "x": CorrectionKind.DEMOTE,
    "thumbsdown": CorrectionKind.DEMOTE,
}

_ACTION_MAP: dict[str, CorrectionKind] = {
    "boost": CorrectionKind.BOOST,
    "save": CorrectionKind.SAVE,
    "skip": CorrectionKind.SKIP,
    "demote": CorrectionKind.DEMOTE,
}

OnCorrection = Callable[[CorrectionEvent], None]


def make_app(
    notifier=None,
    on_correction: OnCorrection | None = None,
) -> App:
    """Build the Slack Bolt app.

    notifier: SlackNotifier — used by `/news demo`.
    on_correction: called with each CorrectionEvent from buttons or reactions.
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
            respond("_Status: M3 pipeline not yet running. Try `/news demo` to preview formatting._")
        else:
            respond("Usage: `/news` · `/news status` · `/news demo`\n_on-demand digest lands in M3._")

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
        kind = _REACTION_MAP.get(event.get("reaction", ""))
        if kind is None or on_correction is None:
            return
        item = event.get("item") or {}
        surface = SurfaceRef(
            surface="slack",
            channel=item.get("channel", ""),
            message_id=item.get("ts", ""),
            posted_at=datetime.now(timezone.utc),
        )
        on_correction(CorrectionEvent(
            article=ArticleId("unknown"),   # ts→ArticleId lookup lands in M5
            topic=TopicId("unknown"),
            source=SourceId("unknown"),
            kind=kind,
            surface=surface,
            user=event.get("user", ""),
            at=datetime.now(timezone.utc),
        ))

    return app


def start(app: App) -> None:
    """Start Socket Mode — blocks until interrupted."""
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
