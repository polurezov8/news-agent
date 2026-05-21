"""Pydantic schemas for all four config files. Single source of validation."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, RootModel


class TagsConfig(BaseModel):
    tags: dict[str, list[str]]


class BoostRule(BaseModel):
    tags: list[str]
    weight: float


class TopicQuery(BaseModel):
    must_have_any: list[str] = Field(default_factory=list)
    must_not_have: list[str] = Field(default_factory=list)
    boost: list[BoostRule] = Field(default_factory=list)
    penalize: list[BoostRule] = Field(default_factory=list)


class TopicRecency(BaseModel):
    half_life_days: int


class TopicDelivery(BaseModel):
    digest_top_n: int = 5
    digest_min_score: float = 0.4
    priority_threshold: float = 0.9
    priority_recency_hours: int = 24


class TopicConfig(BaseModel):
    label: str
    emoji: str
    query: TopicQuery
    recency: TopicRecency
    nl_rules: list[str] = Field(default_factory=list)
    delivery: TopicDelivery = Field(default_factory=TopicDelivery)


class TopicsConfig(BaseModel):
    topics: dict[str, TopicConfig]


class SourceConfig(BaseModel):
    id: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    topics: list[str]
    weight: float = 1.0
    enabled: bool = True
    min_in_digest: int = 0          # guarantees ≥N items from this source in each digest


class SourcesConfig(BaseModel):
    sources: list[SourceConfig]


class PriorEntry(BaseModel):
    source: str
    topic: str
    weight: float


class PriorsConfig(BaseModel):
    priors: list[PriorEntry]
