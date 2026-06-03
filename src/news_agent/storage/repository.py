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
    CorrectionKind,
    ScoreResult,
    SourceId,
    SurfaceRef,
    Tag,
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
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns that CREATE TABLE IF NOT EXISTS can't retrofit onto an
    existing DB. Idempotent — guarded on PRAGMA table_info."""
    _add_column_if_missing(conn, "articles", "read_at", "TEXT")
    _add_column_if_missing(conn, "scores", "taste_adj", "REAL NOT NULL DEFAULT 0")


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, decl: str
) -> None:
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


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


def set_article_read_at(
    conn: sqlite3.Connection,
    article_id: ArticleId,
    read_at: datetime,
) -> None:
    """Stamp an article as read. Used by the Safari sync, which runs outside the
    hash-dedup path so a save→read transition is never dropped before it counts."""
    conn.execute(
        "UPDATE articles SET read_at = ? WHERE id = ?",
        (read_at.isoformat(), str(article_id)),
    )


def load_taste(conn: sqlite3.Connection) -> dict[str, float]:
    """The per-tag interest profile as {tag: weight}."""
    rows = conn.execute("SELECT tag, weight FROM taste").fetchall()
    return {r["tag"]: r["weight"] for r in rows}


def save_taste(
    conn: sqlite3.Connection,
    weights: dict[str, float],
    updated_at: datetime,
) -> None:
    """Overwrite the taste profile with `weights`."""
    for tag, weight in weights.items():
        conn.execute(
            """
            INSERT INTO taste (tag, weight, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(tag) DO UPDATE SET
                weight = excluded.weight,
                updated_at = excluded.updated_at
            """,
            (tag, weight, updated_at.isoformat()),
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


def load_article_tags(conn: sqlite3.Connection, article_id: ArticleId) -> frozenset[Tag]:
    """Tags already persisted for an article. Empty if untagged."""
    rows = conn.execute(
        "SELECT tag FROM tags WHERE article_id = ?", (str(article_id),)
    ).fetchall()
    return frozenset(Tag(r["tag"]) for r in rows)


def get_article_read_state(
    conn: sqlite3.Connection, article_id: ArticleId
) -> tuple[bool, str | None]:
    """(exists, read_at) for an article — lets the Safari sync detect a
    save→read transition without re-tagging or re-counting taste."""
    row = conn.execute(
        "SELECT read_at FROM articles WHERE id = ?", (str(article_id),)
    ).fetchone()
    if row is None:
        return (False, None)
    return (True, row["read_at"])


def save_score(conn: sqlite3.Connection, result: ScoreResult, scored_at: datetime) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO scores
            (article_id, topic_id, substance, tag_adj, decay, source_weight,
             taste_adj, final_score, scored_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(result.article),
            str(result.topic),
            result.substance,
            result.tag_adj,
            result.decay,
            result.source_weight,
            result.taste_adj,
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
        taste_adj=row["taste_adj"],
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
               s.topic_id, s.substance, s.tag_adj, s.decay, s.source_weight,
               s.taste_adj, s.final_score
        FROM scores s
        JOIN articles a ON s.article_id = a.id
        WHERE a.fetched_at >= ?
        ORDER BY s.final_score DESC
        LIMIT ?
        """,
        (since.isoformat(), limit),
    ).fetchall()
    return [(_row_to_article(r), _row_to_score(r, ArticleId(r["id"]))) for r in rows]


def search_articles(
    conn: sqlite3.Connection,
    *,
    query: str,
    topic: str | None = None,
    since: datetime | None = None,
    limit: int = 10,
) -> list[tuple[Article, float | None]]:
    """Free-text search over the corpus for the Slack assistant.

    Matches title/body LIKE %query%, optionally filtered by topic and recency,
    ranked by best final_score then recency. Returns (article, best_final) pairs;
    best_final is None for articles that were never scored (e.g. filtered out)."""
    like = f"%{query}%"
    clauses = ["(a.title LIKE ? OR a.body LIKE ?)"]
    params: list = [like, like]
    if topic:
        clauses.append("s.topic_id = ?")
        params.append(topic)
    if since is not None:
        clauses.append("a.fetched_at >= ?")
        params.append(since.isoformat())
    where = " AND ".join(clauses)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT a.id, a.source_id, a.url, a.title, a.body, a.content_hash,
               a.published_at, a.fetched_at, MAX(s.final_score) AS best_final
        FROM articles a
        LEFT JOIN scores s ON s.article_id = a.id
        WHERE {where}
        GROUP BY a.id
        ORDER BY best_final DESC NULLS LAST, a.fetched_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [(_row_to_article(r), r["best_final"]) for r in rows]


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
    """Look up which (article, topic) a slack message corresponds to.

    Returns one row even when the message contains multiple items — callers that
    need the full set must use `find_surface_targets`.
    """
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


def find_surface_targets(
    conn: sqlite3.Connection,
    channel: str,
    message_id: str,
) -> list[tuple[ArticleId, TopicId]]:
    """All (article, topic) pairs surfaced in the given Slack message.

    A daily digest packs multiple items into one Slack message, so a reaction
    on that message is a signal about every item it contains — return all rows.
    """
    rows = conn.execute(
        """
        SELECT article_id, topic_id FROM surfaces
        WHERE channel = ? AND message_id = ?
        ORDER BY posted_at ASC, id ASC
        """,
        (channel, message_id),
    ).fetchall()
    return [(ArticleId(r["article_id"]), TopicId(r["topic_id"])) for r in rows]


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
    """Cents spent on LLM calls this calendar month (UTC), rounded to the cent.

    Recomputed from the intact token columns via cost_microcents — the stored
    usd_cents column floored every sub-cent call to 0, so summing it gave $0 and
    the budget gate never tripped. Summing precision and dividing once fixes both
    this and the status display retroactively, with no backfill."""
    from news_agent.llm.costs import cost_microcents

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = conn.execute(
        "SELECT model, input_tokens, output_tokens FROM llm_cost WHERE at >= ?",
        (month_start.isoformat(),),
    ).fetchall()
    total_microcents = sum(
        cost_microcents(r["model"], r["input_tokens"], r["output_tokens"]) for r in rows
    )
    return round(total_microcents / 1_000_000)


def save_audit(
    conn: sqlite3.Connection,
    *,
    event: str,
    article_id: str | None,
    payload_json: str,
    at: datetime,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (event, article_id, payload_json, at) VALUES (?, ?, ?, ?)",
        (event, article_id, payload_json, at.isoformat()),
    )


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
               s.topic_id, s.substance, s.tag_adj, s.decay, s.source_weight,
               s.taste_adj, s.final_score
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


def count_distinct_run_days(
    conn: sqlite3.Connection,
    *,
    since: datetime,
    cadence: Cadence,
) -> int:
    """Number of distinct calendar days with at least one surface of `cadence` since `since`."""
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT substr(posted_at, 1, 10)) AS n
        FROM surfaces
        WHERE cadence = ? AND posted_at >= ?
        """,
        (cadence.value, since.isoformat()),
    ).fetchone()
    return int(row["n"] or 0)


def count_surfaces_in_window(
    conn: sqlite3.Connection,
    *,
    since: datetime,
    cadence: Cadence,
) -> int:
    """Total surface rows of the given cadence since `since`."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM surfaces
        WHERE cadence = ? AND posted_at >= ?
        """,
        (cadence.value, since.isoformat()),
    ).fetchone()
    return int(row["n"] or 0)


def count_corrections_by_kind(
    conn: sqlite3.Connection,
    *,
    since: datetime,
    kind: CorrectionKind,
) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM corrections
        WHERE kind = ? AND at >= ?
        """,
        (kind.value, since.isoformat()),
    ).fetchone()
    return int(row["n"] or 0)


def top_sources_in_window(
    conn: sqlite3.Connection,
    *,
    since: datetime,
    cadence: Cadence,
    limit: int = 3,
) -> list[tuple[SourceId, int]]:
    """Top sources by number of surfaces in the window. Returns [(source_id, count), ...]."""
    rows = conn.execute(
        """
        SELECT a.source_id AS source_id, COUNT(*) AS n
        FROM surfaces s
        JOIN articles a ON a.id = s.article_id
        WHERE s.cadence = ? AND s.posted_at >= ?
        GROUP BY a.source_id
        ORDER BY n DESC, a.source_id ASC
        LIMIT ?
        """,
        (cadence.value, since.isoformat(), limit),
    ).fetchall()
    return [(SourceId(r["source_id"]), int(r["n"])) for r in rows]
