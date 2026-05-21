"""SQLite repository. Initialized via schema.sql."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from news_agent.core.types import (
    Article,
    ArticleId,
    Cadence,
    ContentHash,
    CorrectionEvent,
    ScoreResult,
    SourceId,
    SurfaceRef,
    TagResult,
    TopicId,
)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init_db(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA_PATH.read_text())
        conn.commit()
    finally:
        conn.close()


def article_exists_by_hash(conn: sqlite3.Connection, content_hash: ContentHash) -> bool:
    row = conn.execute(
        "SELECT 1 FROM articles WHERE content_hash = ? LIMIT 1",
        (str(content_hash),),
    ).fetchone()
    return row is not None


def upsert_article(conn: sqlite3.Connection, article: Article) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO articles
            (id, source_id, url, title, body, content_hash, published_at, fetched_at, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(article.id),
            str(article.source),
            article.url,
            article.title,
            article.body,
            str(article.content_hash),
            article.published_at.isoformat(),
            article.fetched_at.isoformat(),
            json.dumps(article.raw, default=str),
        ),
    )


def save_tag_result(
    conn: sqlite3.Connection,
    result: TagResult,
    tagged_at: datetime,
    category_lookup: dict[str, str] | None = None,
) -> None:
    lookup = category_lookup or {}
    for tag in result.tags:
        conn.execute(
            """
            INSERT OR REPLACE INTO tags
                (article_id, tag, category, confidence, model, tagged_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(result.article),
                str(tag),
                lookup.get(str(tag), "unknown"),
                result.confidence,
                result.model,
                tagged_at.isoformat(),
            ),
        )


def save_score(conn: sqlite3.Connection, result: ScoreResult, scored_at: datetime) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO scores
            (article_id, topic_id, substance, tag_adj, decay, source_weight, final_score, scored_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(result.article),
            str(result.topic),
            result.substance,
            result.tag_adj,
            result.decay,
            result.source_weight,
            result.final,
            scored_at.isoformat(),
        ),
    )


def save_surface(
    conn: sqlite3.Connection,
    article_id: ArticleId,
    topic_id: TopicId,
    ref: SurfaceRef,
    cadence: Cadence,
) -> None:
    conn.execute(
        """
        INSERT INTO surfaces
            (article_id, topic_id, cadence, surface, channel, message_id, posted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(article_id),
            str(topic_id),
            cadence.value,
            ref.surface,
            ref.channel,
            ref.message_id,
            ref.posted_at.isoformat(),
        ),
    )


def get_source_prior(
    conn: sqlite3.Connection, source: SourceId, topic: TopicId
) -> float | None:
    row = conn.execute(
        "SELECT weight FROM source_priors WHERE source_id = ? AND topic_id = ?",
        (str(source), str(topic)),
    ).fetchone()
    return row["weight"] if row else None


def has_surface(
    conn: sqlite3.Connection,
    article_id: ArticleId,
    topic_id: TopicId,
    cadence: Cadence,
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM surfaces WHERE article_id = ? AND topic_id = ? AND cadence = ? LIMIT 1",
        (str(article_id), str(topic_id), cadence.value),
    ).fetchone()
    return row is not None


def _row_to_article(row: sqlite3.Row) -> Article:
    return Article(
        id=ArticleId(row["id"]),
        source=SourceId(row["source_id"]),
        url=row["url"],
        title=row["title"],
        body=row["body"],
        content_hash=ContentHash(row["content_hash"]),
        published_at=datetime.fromisoformat(row["published_at"]),
        fetched_at=datetime.fromisoformat(row["fetched_at"]),
        raw={},
    )


def _row_to_score(row: sqlite3.Row, article_id: ArticleId) -> ScoreResult:
    return ScoreResult(
        article=article_id,
        topic=TopicId(row["topic_id"]),
        substance=row["substance"],
        tag_adj=row["tag_adj"],
        decay=row["decay"],
        source_weight=row["source_weight"],
        final=row["final_score"],
    )


def query_top_scored(
    conn: sqlite3.Connection,
    *,
    since: datetime,
    limit: int = 10,
) -> list[tuple[Article, ScoreResult]]:
    """Top-scored (article, score) pairs fetched after `since`, ordered by final_score desc."""
    rows = conn.execute(
        """
        SELECT a.id, a.source_id, a.url, a.title, a.body, a.content_hash,
               a.published_at, a.fetched_at,
               s.topic_id, s.substance, s.tag_adj, s.decay, s.source_weight, s.final_score
        FROM scores s
        JOIN articles a ON s.article_id = a.id
        WHERE a.fetched_at >= ?
        ORDER BY s.final_score DESC
        LIMIT ?
        """,
        (since.isoformat(), limit),
    ).fetchall()
    return [(_row_to_article(r), _row_to_score(r, ArticleId(r["id"]))) for r in rows]


def save_correction(conn: sqlite3.Connection, event: CorrectionEvent) -> None:
    conn.execute(
        """
        INSERT INTO corrections
            (article_id, topic_id, source_id, kind, new_topic_id, user, surface, at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(event.article),
            str(event.topic),
            str(event.source),
            event.kind.value,
            str(event.new_topic) if event.new_topic else None,
            event.user,
            event.surface.surface,
            event.at.isoformat(),
        ),
    )


def upsert_source_prior(
    conn: sqlite3.Connection,
    source: SourceId,
    topic: TopicId,
    weight: float,
    updated_at: datetime,
) -> None:
    conn.execute(
        """
        INSERT INTO source_priors (source_id, topic_id, weight, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(source_id, topic_id) DO UPDATE SET
            weight = excluded.weight,
            updated_at = excluded.updated_at
        """,
        (str(source), str(topic), weight, updated_at.isoformat()),
    )


def find_surface_target(
    conn: sqlite3.Connection,
    channel: str,
    message_id: str,
) -> tuple[ArticleId, TopicId] | None:
    """Look up which (article, topic) a slack message corresponds to."""
    row = conn.execute(
        """
        SELECT article_id, topic_id FROM surfaces
        WHERE channel = ? AND message_id = ?
        ORDER BY posted_at DESC LIMIT 1
        """,
        (channel, message_id),
    ).fetchone()
    if not row:
        return None
    return ArticleId(row["article_id"]), TopicId(row["topic_id"])


def save_llm_cost(
    conn: sqlite3.Connection,
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    usd_cents: int,
    purpose: str,
    article_id: str | None,
    at: datetime,
) -> None:
    conn.execute(
        """
        INSERT INTO llm_cost
            (model, input_tokens, output_tokens, usd_cents, article_id, purpose, at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (model, input_tokens, output_tokens, usd_cents, article_id, purpose, at.isoformat()),
    )


def monthly_usd_cents(conn: sqlite3.Connection, *, now: datetime) -> int:
    """Sum llm_cost.usd_cents for the current calendar month (UTC)."""
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    row = conn.execute(
        "SELECT COALESCE(SUM(usd_cents), 0) AS c FROM llm_cost WHERE at >= ?",
        (month_start.isoformat(),),
    ).fetchone()
    return int(row["c"])


def load_priors_dict(conn: sqlite3.Connection) -> dict[tuple[str, str], float]:
    rows = conn.execute(
        "SELECT source_id, topic_id, weight FROM source_priors"
    ).fetchall()
    return {(r["source_id"], r["topic_id"]): r["weight"] for r in rows}


def query_skipped_high(
    conn: sqlite3.Connection,
    *,
    since: datetime,
    min_score: float = 0.85,
    limit: int = 20,
) -> list[tuple[Article, ScoreResult]]:
    """High-scored items that were NEVER posted to any surface within the window."""
    rows = conn.execute(
        """
        SELECT a.id, a.source_id, a.url, a.title, a.body, a.content_hash,
               a.published_at, a.fetched_at,
               s.topic_id, s.substance, s.tag_adj, s.decay, s.source_weight, s.final_score
        FROM scores s
        JOIN articles a ON s.article_id = a.id
        LEFT JOIN surfaces sur
            ON sur.article_id = s.article_id AND sur.topic_id = s.topic_id
        WHERE a.fetched_at >= ?
          AND s.final_score >= ?
          AND sur.id IS NULL
        ORDER BY s.final_score DESC
        LIMIT ?
        """,
        (since.isoformat(), min_score, limit),
    ).fetchall()
    return [(_row_to_article(r), _row_to_score(r, ArticleId(r["id"]))) for r in rows]
