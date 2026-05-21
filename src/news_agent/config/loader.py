"""YAML config loader. Validates against pydantic schemas."""

from __future__ import annotations

from pathlib import Path

import yaml

from .schema import PriorsConfig, SourcesConfig, TagsConfig, TopicsConfig

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


def _load_yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f) or {}


def load_tags(config_dir: Path = DEFAULT_CONFIG_DIR) -> TagsConfig:
    return TagsConfig(**_load_yaml(config_dir / "tags.yaml"))


def load_topics(config_dir: Path = DEFAULT_CONFIG_DIR) -> TopicsConfig:
    data = _load_yaml(config_dir / "topics.yaml")
    return TopicsConfig(topics=data.get("topics") or {})


def load_sources(config_dir: Path = DEFAULT_CONFIG_DIR) -> SourcesConfig:
    data = _load_yaml(config_dir / "sources.yaml")
    return SourcesConfig(sources=data.get("sources") or [])


def load_priors(config_dir: Path = DEFAULT_CONFIG_DIR) -> PriorsConfig:
    data = _load_yaml(config_dir / "source_priors.yaml")
    return PriorsConfig(priors=data.get("priors") or [])
