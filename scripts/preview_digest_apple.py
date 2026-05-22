"""One-shot Slack preview of the Apple-style digest design.

Validates the proposed layout in the actual delivery channel before any package
code changes. Sends a single sample message to the user's DM. Throwaway script —
delete after the design is locked in.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


def _section(title: str, url: str, meta: str) -> dict:
    return {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*<{url}|{title}>*\n_{meta}_",
        },
    }


def _label(glyph: str, text: str) -> dict:
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"*{glyph}  {text}*"}],
    }


def build_blocks() -> list[dict]:
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Friday, May 22", "emoji": True},
        },
        _label("⭐️", "EDITOR'S PICK"),
        _section(
            title="Scaling Laws for Neural Language Models",
            url="https://arxiv.org/abs/2001.08361",
            meta="arxiv · AI · this morning",
        ),
        _label("✨", "ALSO TODAY"),
        _section(
            title="SwiftUI для справжніх macOS-застосунків",
            url="https://t.me/iOSDevsUA/982",
            meta="iOSDevsUA · iOS · this morning",
        ),
        _section(
            title="How Shopify ships big features in small slices",
            url="https://dou.ua/lenta/articles/shopify-slicing",
            meta="DOU · Engineering Leadership · 4h ago",
        ),
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "1531 scanned · 9 on topic · 3 picks",
                }
            ],
        },
    ]


def main() -> int:
    token = os.environ.get("SLACK_BOT_TOKEN")
    user_id = os.environ.get("SLACK_USER_ID")
    if not token or not user_id:
        print("missing SLACK_BOT_TOKEN or SLACK_USER_ID in .env", file=sys.stderr)
        return 1

    client = WebClient(token=token)
    resp = client.conversations_open(users=[user_id])
    channel = resp["channel"]["id"]

    try:
        client.chat_postMessage(
            channel=channel,
            blocks=build_blocks(),
            text="Daily Digest — preview (Apple style)",
            unfurl_links=False,
            unfurl_media=False,
        )
    except SlackApiError as exc:
        print(f"Slack error: {exc.response.get('error')}", file=sys.stderr)
        print(f"detail: {exc.response.get('response_metadata', {})}", file=sys.stderr)
        return 2

    print("preview sent ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
