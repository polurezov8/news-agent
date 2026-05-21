"""Source protocol + registry + factory.

Add new source type:
  1. Implement class w/ `id`, `topics`, `weight`, `fetch()`, and `from_config()`.
  2. Decorate with `@register_source("<type>")`.
  3. That's it. No factory edits needed.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from news_agent.config.schema import SourceConfig
from news_agent.core.types import Article, SourceId, TopicId


@runtime_checkable
class Source(Protocol):
    id: SourceId
    topics: list[TopicId]
    weight: float

    def fetch(self) -> list[Article]: ...


SOURCE_REGISTRY: dict[str, type] = {}


def register_source(type_name: str):
    def deco(cls):
        SOURCE_REGISTRY[type_name] = cls
        return cls
    return deco


def make_source(cfg: SourceConfig) -> Source:
    """Resolve SourceConfig.type via registry; instantiate via from_config."""
    if cfg.type not in SOURCE_REGISTRY:
        raise ValueError(
            f"Unknown source type: {cfg.type!r}. "
            f"Registered: {sorted(SOURCE_REGISTRY)}"
        )
    cls = SOURCE_REGISTRY[cfg.type]
    return cls.from_config(cfg)
