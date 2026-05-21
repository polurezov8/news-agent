"""Type-driven core: NewType IDs and discriminated unions for the pipeline.

Illegal states unrepresentable. IDs are not interchangeable strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal, NewType

ArticleId = NewType("ArticleId", str)
SourceId = NewType("SourceId", str)
TopicId = NewType("TopicId", str)
Tag = NewType("Tag", str)
ContentHash = NewType("ContentHash", str)


class TagCategory(str, Enum):
    DOMAIN = "domain"
    TYPE = "type"
    QUALITY = "quality"


@dataclass(frozen=True, slots=True)
class Article:
    id: ArticleId
    source: SourceId
    url: str
    title: str
    body: str
    content_hash: ContentHash
    published_at: datetime
    fetched_at: datetime
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TagResult:
    article: ArticleId
    tags: frozenset[Tag]
    confidence: float
    model: str


@dataclass(frozen=True, slots=True)
class ScoreResult:
    article: ArticleId
    topic: TopicId
    substance: float          # 0..1 from Sonnet
    tag_adj: float            # ± from boost / penalize
    decay: float              # 0..1 from per-topic half-life
    source_weight: float      # 0..1 from source_priors
    final: float              # (substance + tag_adj) * decay * source_weight


class Cadence(str, Enum):
    DAILY = "daily"
    PRIORITY = "priority"
    WEEKLY = "weekly"
    ON_DEMAND = "on_demand"


@dataclass(frozen=True, slots=True)
class SurfaceRef:
    """Reference to a posted message; pluggable across notifiers."""

    surface: Literal["slack", "telegram"]
    channel: str
    message_id: str
    posted_at: datetime


class CorrectionKind(str, Enum):
    BOOST = "boost"
    DEMOTE = "demote"
    RETAG = "retag"
    SAVE = "save"
    SKIP = "skip"


@dataclass(frozen=True, slots=True)
class CorrectionEvent:
    article: ArticleId
    topic: TopicId
    source: SourceId
    kind: CorrectionKind
    surface: SurfaceRef
    user: str
    at: datetime
    new_topic: TopicId | None = None   # for RETAG
