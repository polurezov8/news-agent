"""Transactor seam — pluggable persistence behind the pipeline nodes.

Before this module, tag_node, score_node, and notify each carried an
`if deps.dry_run` branch around their DB writes. Adding a Transactor
turns dry-run into adapter selection at construction time:

    SqliteTransactor  → writes to sqlite (default)
    NullTransactor    → no-op (dry-run, tests)

Two adapters = real seam. Nodes call transactor.persist_*; the dry-run
branching is gone from node bodies.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from ..core.types import Article, ArticleId, Cadence, ScoreResult, SurfaceRef, Tag, TagResult, TopicId
from ..storage.repository import (
    connect,
    save_score,
    save_surface,
    save_tag_result,
    upsert_article,
)


class Transactor(Protocol):
    """Pluggable persistence interface for the pipeline.

    Implementations: SqliteTransactor (real), NullTransactor (no-op for
    dry-runs and tests).
    """

    @property
    def writes(self) -> bool:
        """True if persist_* calls reach durable storage. UI hint only."""
        ...

    def persist_tags(
        self,
        tag_results: list[TagResult],
        articles_by_id: dict[str, Article],
        category_lookup: dict[str, str],
        when: datetime,
    ) -> None: ...

    def persist_scores(self, score_results: list[ScoreResult], when: datetime) -> None: ...

    def persist_surface(
        self,
        article: ArticleId,
        topic: TopicId,
        surface_ref: SurfaceRef,
        cadence: Cadence,
    ) -> None: ...


class SqliteTransactor(Transactor):
    """Writes pipeline state to sqlite. One short-lived conn per call.

    Short-lived connections (open/commit/close per method) match the existing
    node pattern and avoid holding a writer while the CountingClient writes
    cost rows on its own connection.
    """

    writes = True

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def persist_tags(
        self,
        tag_results: list[TagResult],
        articles_by_id: dict[str, Article],
        category_lookup: dict[str, str],
        when: datetime,
    ) -> None:
        conn = connect(self._db_path)
        try:
            for r in tag_results:
                article = articles_by_id.get(str(r.article))
                if article is None:
                    continue
                upsert_article(conn, article)
                save_tag_result(conn, r, when, category_lookup=category_lookup)
            conn.commit()
        finally:
            conn.close()

    def persist_scores(self, score_results: list[ScoreResult], when: datetime) -> None:
        conn = connect(self._db_path)
        try:
            for sr in score_results:
                save_score(conn, sr, when)
            conn.commit()
        finally:
            conn.close()

    def persist_surface(
        self,
        article: ArticleId,
        topic: TopicId,
        surface_ref: SurfaceRef,
        cadence: Cadence,
    ) -> None:
        conn = connect(self._db_path)
        try:
            save_surface(conn, article, topic, surface_ref, cadence)
            conn.commit()
        finally:
            conn.close()


class NullTransactor(Transactor):
    """No-op persistence. Used for dry-runs and tests.

    An optional log callback receives a one-line message per skipped batch so
    callers can confirm the pipeline reached the persistence step.
    """

    writes = False

    def __init__(self, log: Callable[[str], None] | None = None) -> None:
        self._log = log or (lambda _msg: None)

    def persist_tags(
        self,
        tag_results: list[TagResult],
        articles_by_id: dict[str, Article],
        category_lookup: dict[str, str],
        when: datetime,
    ) -> None:
        self._log(f"persist_tags: {len(tag_results)} skipped (dry-run)")

    def persist_scores(self, score_results: list[ScoreResult], when: datetime) -> None:
        self._log(f"persist_scores: {len(score_results)} skipped (dry-run)")

    def persist_surface(
        self,
        article: ArticleId,
        topic: TopicId,
        surface_ref: SurfaceRef,
        cadence: Cadence,
    ) -> None:
        self._log(f"persist_surface: {cadence.value} for {article} skipped (dry-run)")
