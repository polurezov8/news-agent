"""Notifier protocol. SlackNotifier v1.0; TelegramNotifier v1.1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from news_agent.core.types import (
    Article,
    CorrectionEvent,
    ScoreResult,
    SurfaceRef,
    TopicId,
)


@dataclass(frozen=True, slots=True)
class DigestPayload:
    items: list[tuple[Article, ScoreResult]]
    topic_order: list[TopicId]
    topic_labels: dict[TopicId, tuple[str, str]] = field(default_factory=dict)
    """topic_id → (emoji, label). Optional; renderer falls back to id if missing."""


@dataclass(frozen=True, slots=True)
class PriorityPayload:
    article: Article
    score: ScoreResult


@dataclass(frozen=True, slots=True)
class RecapPayload:
    top_items: list[tuple[Article, ScoreResult]]
    skipped_but_high: list[tuple[Article, ScoreResult]]
    window_days: int


@runtime_checkable
class Notifier(Protocol):
    def post_digest(self, payload: DigestPayload) -> SurfaceRef: ...
    def dm_priority(self, payload: PriorityPayload) -> SurfaceRef: ...
    def post_recap(self, payload: RecapPayload) -> SurfaceRef: ...
    def handle_correction(self, event: CorrectionEvent) -> None: ...
