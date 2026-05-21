"""Importing this package populates SOURCE_REGISTRY via @register_source side effects."""

from . import (  # noqa: F401
    arxiv,
    github_trending,
    hackernews,
    lobsters,
    rss,
    rsshub,
    rsshub_twitter_list,
    safari_reading_list,
)
from .base import SOURCE_REGISTRY, Source, make_source, register_source

__all__ = ["SOURCE_REGISTRY", "Source", "make_source", "register_source"]
