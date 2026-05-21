"""Slack notifier — Block Kit formatting + WebClient posting."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

from news_agent.core.types import (
    Article,
    ArticleId,
    ContentHash,
    CorrectionEvent,
    ScoreResult,
    SourceId,
    SurfaceRef,
    TopicId,
)
from news_agent.notifier.base import DigestPayload, PriorityPayload, RecapPayload


def _safe_link_text(s: str) -> str:
    """Inside Slack `<url|text>`, these chars break parsing. Replace, don't escape."""
    return s.replace("<", "‹").replace(">", "›").replace("|", "｜")


def _article_blocks(article: Article, score: ScoreResult) -> list[dict]:
    """Block Kit blocks for one scored article: section + actions."""
    bar = "█" * int(score.final * 10) + "░" * (10 - int(score.final * 10))
    title_safe = _safe_link_text(article.title)
    text = (
        f"*<{article.url}|{title_safe}>*\n"
        f"`{article.source}` · score {score.final:.2f} [{bar}]"
    )
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "actions",
            "block_id": f"a:{article.id}|{score.topic}",
            "elements": [
                _btn("👍 Boost", "boost", article.id, score.topic),
                _btn("🔖 Save", "save", article.id, score.topic),
                _btn("💤 Skip", "skip", article.id, score.topic),
                _btn("❌ Demote", "demote", article.id, score.topic, style="danger"),
            ],
        },
    ]


def _btn(
    label: str,
    action_id: str,
    article_id: ArticleId,
    topic: TopicId,
    *,
    style: str | None = None,
) -> dict:
    el: dict = {
        "type": "button",
        "text": {"type": "plain_text", "text": label, "emoji": True},
        "action_id": action_id,
        "value": f"{article_id}|{topic}",
    }
    if style:
        el["style"] = style
    return el


def digest_blocks(payload: DigestPayload) -> list[dict]:
    """Block Kit blocks for a full digest grouped by topic."""
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📰 Daily Digest — {datetime.now(timezone.utc).strftime('%B %d, %Y')}",
                "emoji": True,
            },
        }
    ]

    by_topic: dict[TopicId, list[tuple[Article, ScoreResult]]] = {}
    for article, score in payload.items:
        by_topic.setdefault(score.topic, []).append((article, score))

    for topic in payload.topic_order:
        items = by_topic.get(topic, [])
        if not items:
            continue
        emoji, label = payload.topic_labels.get(topic, ("", str(topic)))
        header_text = f"{emoji} *{label}*".strip()
        blocks.append({"type": "divider"})
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": header_text}})
        for article, score in items:
            blocks.extend(_article_blocks(article, score))

    return blocks


def priority_blocks(payload: PriorityPayload) -> list[dict]:
    """Block Kit blocks for a priority DM."""
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🔔 Priority Item", "emoji": True},
        },
        *_article_blocks(payload.article, payload.score),
    ]


def recap_blocks(payload: RecapPayload) -> list[dict]:
    """Block Kit blocks for a weekly recap."""
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📊 Weekly Recap — last {payload.window_days} days",
                "emoji": True,
            },
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Top {len(payload.top_items)} this week:*"}},
    ]
    for article, score in payload.top_items:
        blocks.extend(_article_blocks(article, score))

    if payload.skipped_but_high:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*High-score but skipped ({len(payload.skipped_but_high)}):*"},
        })
        for article, score in payload.skipped_but_high:
            blocks.extend(_article_blocks(article, score))

    return blocks


def _demo_payload() -> DigestPayload:
    """Sample payload for `/news demo` and unit tests."""
    now = datetime.now(timezone.utc)
    rows = [
        ("demo:aaa111bbb222cccc", "hn", "Demo: What I Learned Shipping a Solo Product", "https://news.ycombinator.com/item?id=1", "topic_a", 0.87),
        ("demo:ddd333eee444ffff", "arxiv", "Demo: Scaling Laws for Neural Language Models", "https://arxiv.org/abs/2001.08361", "topic_a", 0.74),
        ("demo:ggg555hhh666iiii", "lobsters", "Demo: Writing a Compiler in 500 Lines", "https://lobste.rs/s/example", "topic_b", 0.61),
    ]
    items: list[tuple[Article, ScoreResult]] = []
    for aid, src, title, url, topic, final in rows:
        ch = ContentHash(aid.split(":")[1])
        article = Article(
            id=ArticleId(aid),
            source=SourceId(src),
            url=url,
            title=title,
            body="",
            content_hash=ch,
            published_at=now,
            fetched_at=now,
        )
        score = ScoreResult(
            article=ArticleId(aid),
            topic=TopicId(topic),
            substance=final,
            tag_adj=0.0,
            decay=1.0,
            source_weight=1.0,
            final=final,
        )
        items.append((article, score))

    return DigestPayload(items=items, topic_order=[TopicId("topic_a"), TopicId("topic_b")])


@dataclass
class SlackNotifier:
    bot_token: str
    user_id: str

    def __post_init__(self) -> None:
        from slack_sdk import WebClient

        self._client = WebClient(token=self.bot_token)
        resp = self._client.conversations_open(users=[self.user_id])
        self._dm_channel: str = resp["channel"]["id"]

    _BLOCK_LIMIT = 50

    def _post(self, blocks: list[dict], text: str) -> SurfaceRef:
        from slack_sdk.errors import SlackApiError

        try:
            resp = self._client.chat_postMessage(
                channel=self._dm_channel,
                blocks=blocks,
                text=text,
                unfurl_links=False,
                unfurl_media=False,
            )
        except SlackApiError as exc:
            err = exc.response.get("error", "?")
            meta = exc.response.get("response_metadata", {})
            messages = meta.get("messages") or []
            detail = "; ".join(messages) if messages else "no detail"
            raise RuntimeError(
                f"Slack post failed: {err} — {detail}\nblocks: {blocks}"
            ) from exc
        return SurfaceRef(
            surface="slack",
            channel=self._dm_channel,
            message_id=resp["ts"],
            posted_at=datetime.now(timezone.utc),
        )

    def _chunked_post(self, blocks: list[dict], text: str) -> SurfaceRef:
        """Split blocks into ≤50-block messages; return SurfaceRef of first."""
        chunks = [blocks[i:i + self._BLOCK_LIMIT] for i in range(0, len(blocks), self._BLOCK_LIMIT)]
        first: SurfaceRef | None = None
        for chunk in chunks:
            ref = self._post(chunk, text)
            if first is None:
                first = ref
        return first  # type: ignore[return-value]  # always at least one chunk

    def post_digest(self, payload: DigestPayload) -> SurfaceRef:
        return self._chunked_post(digest_blocks(payload), f"Daily Digest — {len(payload.items)} items")

    def dm_priority(self, payload: PriorityPayload) -> SurfaceRef:
        return self._post(priority_blocks(payload), f"Priority: {payload.article.title}")

    def post_recap(self, payload: RecapPayload) -> SurfaceRef:
        return self._chunked_post(recap_blocks(payload), f"Weekly Recap — {payload.window_days} days")

    def handle_correction(self, event: CorrectionEvent) -> None:
        # Corrections flow in via Slack events (listener.py), not out. No-op here.
        pass

    @classmethod
    def from_env(cls) -> "SlackNotifier":
        return cls(
            bot_token=os.environ["SLACK_BOT_TOKEN"],
            user_id=os.environ["SLACK_USER_ID"],
        )
