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
    PipelineCounters,
    ScoreResult,
    SourceId,
    SurfaceRef,
    TopicId,
)
from news_agent.notifier._time import relative_time
from news_agent.notifier.base import (
    DigestPayload,
    PriorityPayload,
    RecapPayload,
    WeeklyStats,
)


def _counters_summary(counters: PipelineCounters) -> str:
    """Compact daily-digest footer: scanned · on topic · picks. No emojis."""
    return (
        f"{counters.fetched} scanned · "
        f"{counters.on_topic} on topic · "
        f"{counters.surfaced} picks"
    )


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


_EDITOR_LABEL = "*⭐️  EDITOR'S PICK*"
_ALSO_LABEL = "*✨  ALSO TODAY*"


def _label_context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def _item_section(
    article: Article,
    score: ScoreResult,
    topic_labels: dict[TopicId, tuple[str, str]],
    *,
    now: datetime,
) -> dict:
    title_safe = _safe_link_text(article.title)
    _emoji, topic_label = topic_labels.get(score.topic, ("", str(score.topic)))
    meta = f"{article.source} · {topic_label} · {relative_time(article.published_at, now=now)}"
    return {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*<{article.url}|{title_safe}>*\n_{meta}_",
        },
    }


def digest_blocks(payload: DigestPayload) -> list[dict]:
    """Block Kit blocks for the Apple-style daily digest.

    Header (date) → EDITOR'S PICK label → highest-final section →
    ALSO TODAY label (if 2+ items) → remaining sections by final desc →
    compact footer (if counters set).
    """
    now = datetime.now(timezone.utc)
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": now.strftime("%A, %B %-d"),
                "emoji": True,
            },
        }
    ]

    ranked = sorted(payload.items, key=lambda x: x[1].final, reverse=True)
    if not ranked:
        return blocks

    blocks.append(_label_context(_EDITOR_LABEL))
    blocks.append(_item_section(ranked[0][0], ranked[0][1], payload.topic_labels, now=now))

    if len(ranked) > 1:
        blocks.append(_label_context(_ALSO_LABEL))
        for article, score in ranked[1:]:
            blocks.append(_item_section(article, score, payload.topic_labels, now=now))

    if payload.counters is not None:
        blocks.append(_label_context(_counters_summary(payload.counters)))

    return blocks


_PRIORITY_LABEL = "*🔔  PRIORITY*"


def priority_blocks(payload: PriorityPayload) -> list[dict]:
    """Apple-style priority DM: label + single item section. No actions, no score bar."""
    now = datetime.now(timezone.utc)
    return [
        _label_context(_PRIORITY_LABEL),
        _item_section(payload.article, payload.score, payload.topic_labels, now=now),
    ]


_TOP_WEEK_LABEL = "*⭐️  TOP THIS WEEK*"
_SKIPPED_LABEL = "*💭  HIGH-SCORE BUT SKIPPED*"


def _weekly_stats_footer(stats: "WeeklyStats") -> str:
    parts = [
        f"{stats.runs} runs",
        f"{stats.surfaced} surfaced",
        f"{stats.boosted} boosted",
        f"{stats.demoted} demoted",
    ]
    if stats.top_sources:
        top_names = ", ".join(s for s, _ in stats.top_sources)
        parts.append(f"top sources: {top_names}")
    return " · ".join(parts)


def recap_blocks(payload: RecapPayload) -> list[dict]:
    """Apple-style weekly recap: header date, TOP/SKIPPED labels, weekly stats footer."""
    now = datetime.now(timezone.utc)
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{now.strftime('%a, %b %-d')} — week recap",
                "emoji": True,
            },
        }
    ]

    if payload.top_items:
        blocks.append(_label_context(_TOP_WEEK_LABEL))
        for article, score in payload.top_items:
            blocks.append(_item_section(article, score, payload.topic_labels, now=now))

    if payload.skipped_but_high:
        blocks.append(_label_context(_SKIPPED_LABEL))
        for article, score in payload.skipped_but_high:
            blocks.append(_item_section(article, score, payload.topic_labels, now=now))

    if payload.stats is not None:
        blocks.append(_label_context(_weekly_stats_footer(payload.stats)))

    return blocks


def _demo_payload() -> DigestPayload:
    """Sample payload for `/news demo` and unit tests."""
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    rows = [
        ("demo:aaa111bbb222cccc", "hn", "Demo: What I Learned Shipping a Solo Product", "https://news.ycombinator.com/item?id=1", "ai", 0.87, timedelta(minutes=20)),
        ("demo:ddd333eee444ffff", "arxiv", "Demo: Scaling Laws for Neural Language Models", "https://arxiv.org/abs/2001.08361", "ai", 0.74, timedelta(hours=4)),
        ("demo:ggg555hhh666iiii", "lobsters", "Demo: Writing a Compiler in 500 Lines", "https://lobste.rs/", "ios", 0.61, timedelta(days=2)),
    ]
    items: list[tuple[Article, ScoreResult]] = []
    for aid, src, title, url, topic, final, age in rows:
        ch = ContentHash(aid.split(":")[1])
        article = Article(
            id=ArticleId(aid),
            source=SourceId(src),
            url=url,
            title=title,
            body="",
            content_hash=ch,
            published_at=now - age,
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

    return DigestPayload(
        items=items,
        topic_order=[TopicId("ai"), TopicId("ios")],
        topic_labels={
            TopicId("ai"): ("🤖", "AI"),
            TopicId("ios"): ("📱", "iOS"),
        },
        counters=PipelineCounters(
            fetched=1531, new=11, tagged=11, on_topic=9, scored=9, surfaced=3,
        ),
    )


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
