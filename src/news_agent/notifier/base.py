"""Notifier protocol. SlackNotifier v1.0; TelegramNotifier v1.1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from news_agent.core.types import (
    Article,
    CorrectionEvent,
    PipelineCounters,
    ScoreResult,
    SourceId,
    SurfaceRef,
    TopicId,
)


@dataclass(frozen=True, slots=True)
class DigestPayload:
    items: list[tuple[Article, ScoreResult]]
    topic_order: list[TopicId]
    topic_labels: dict[TopicId, tuple[str, str]] = field(default_factory=dict)
    """topic_id → (emoji, label). Optional; renderer falls back to id if missing."""
    counters: PipelineCounters | None = None
    """Pipeline funnel counts for the run; rendered under digest header when present."""


@dataclass(frozen=True, slots=True)
class PriorityPayload:
    article: Article
    score: ScoreResult
    topic_labels: dict[TopicId, tuple[str, str]] = field(default_factory=dict)
    """topic_id → (emoji, label). Used for the meta line; falls back to topic id."""


@dataclass(frozen=True, slots=True)
class WeeklyStats:
    runs: int
    surfaced: int
    boosted: int
    demoted: int
    top_sources: list[tuple[SourceId, int]]


@dataclass(frozen=True, slots=True)
class RecapPayload:
    top_items: list[tuple[Article, ScoreResult]]
    skipped_but_high: list[tuple[Article, ScoreResult]]
    window_days: int
    topic_labels: dict[TopicId, tuple[str, str]] = field(default_factory=dict)
    stats: WeeklyStats | None = None
    taste_top: list[tuple[str, float]] = field(default_factory=list)
    """Strongest reading-list interest tags — rendered as 'from your reading list'."""
    uncovered_tags: list[str] = field(default_factory=list)
    """High-interest tags no topic captures — surfaced as topic suggestions."""


@runtime_checkable
class Notifier(Protocol):
    def post_digest(self, payload: DigestPayload) -> SurfaceRef: ...
    def dm_priority(self, payload: PriorityPayload) -> SurfaceRef: ...
    def post_recap(self, payload: RecapPayload) -> SurfaceRef: ...
    def handle_correction(self, event: CorrectionEvent) -> None: ...
