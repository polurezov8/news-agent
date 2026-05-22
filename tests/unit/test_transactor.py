"""Tests for the Transactor seam — pluggable persistence behind the pipeline."""

from __future__ import annotations

from datetime import datetime, timezone


from news_agent.core.types import (
    Article,
    ArticleId,
    Cadence,
    ContentHash,
    ScoreResult,
    SourceId,
    SurfaceRef,
    Tag,
    TagResult,
    TopicId,
)
from news_agent.pipeline.transactor import NullTransactor, SqliteTransactor
from news_agent.storage.repository import (
    article_exists_by_hash,
    connect,
    init_db,
)


NOW = datetime(2026, 5, 23, 0, 0, tzinfo=timezone.utc)


def _article(aid: str) -> Article:
    return Article(
        id=ArticleId(f"src:{aid}"),
        source=SourceId("src"),
        url="https://x",
        title="t",
        body="b",
        content_hash=ContentHash(aid.ljust(12, "0")),
        published_at=NOW,
        fetched_at=NOW,
    )


def _tag_result(article: Article) -> TagResult:
    return TagResult(
        article=article.id,
        tags=frozenset([Tag("ai")]),
        confidence=1.0,
        model="haiku-test",
    )


def _score_result(article: Article, topic: str = "topic_a") -> ScoreResult:
    return ScoreResult(
        article=article.id,
        topic=TopicId(topic),
        substance=0.8,
        tag_adj=0.0,
        decay=1.0,
        source_weight=1.0,
        final=0.8,
    )


# ---------------------------------------------------------------- #
# Interface contract — both adapters expose the same shape.
# ---------------------------------------------------------------- #


def test_null_transactor_advertises_no_writes():
    assert NullTransactor().writes is False


def test_sqlite_transactor_advertises_writes(tmp_path):
    assert SqliteTransactor(tmp_path / "t.db").writes is True


# ---------------------------------------------------------------- #
# NullTransactor — no-ops, optional logging.
# ---------------------------------------------------------------- #


def test_null_transactor_does_not_write(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    a = _article("a")
    t = NullTransactor()
    t.persist_tags([_tag_result(a)], {str(a.id): a}, {"ai": "domain"}, NOW)
    t.persist_scores([_score_result(a)], NOW)
    t.persist_surface(
        a.id, TopicId("topic_a"),
        SurfaceRef(surface="slack", channel="D0X", message_id="t", posted_at=NOW),
        Cadence.DAILY,
    )

    conn = connect(db)
    try:
        # No tables written
        articles = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        tags = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        scores = conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0]
        surfaces = conn.execute("SELECT COUNT(*) FROM surfaces").fetchone()[0]
    finally:
        conn.close()
    assert articles == 0
    assert tags == 0
    assert scores == 0
    assert surfaces == 0


def test_null_transactor_log_callback_invoked():
    captured: list[str] = []
    t = NullTransactor(log=captured.append)
    a = _article("a")
    t.persist_tags([_tag_result(a)], {str(a.id): a}, {}, NOW)
    t.persist_scores([_score_result(a)], NOW)
    assert any("persist_tags" in m for m in captured)
    assert any("persist_scores" in m for m in captured)


# ---------------------------------------------------------------- #
# SqliteTransactor — actually writes.
# ---------------------------------------------------------------- #


def test_sqlite_transactor_persists_tags_and_articles(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    a = _article("a")
    SqliteTransactor(db).persist_tags(
        [_tag_result(a)], {str(a.id): a}, {"ai": "domain"}, NOW,
    )
    conn = connect(db)
    try:
        assert article_exists_by_hash(conn, a.content_hash) is True
        tags = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    finally:
        conn.close()
    assert tags == 1


def test_sqlite_transactor_persist_tags_drops_unknown_article(tmp_path):
    """Tag result for an article not in the by-id dict is silently dropped."""
    db = tmp_path / "t.db"
    init_db(db)
    a = _article("a")  # NOT placed in articles_by_id
    SqliteTransactor(db).persist_tags(
        [_tag_result(a)], articles_by_id={}, category_lookup={}, when=NOW,
    )
    conn = connect(db)
    try:
        articles = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        tags = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    finally:
        conn.close()
    assert articles == 0
    assert tags == 0


def test_sqlite_transactor_persists_scores(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    a = _article("a")
    # Score row needs article + tag rows in place due to schema FKs.
    transactor = SqliteTransactor(db)
    transactor.persist_tags([_tag_result(a)], {str(a.id): a}, {"ai": "domain"}, NOW)
    transactor.persist_scores([_score_result(a)], NOW)
    conn = connect(db)
    try:
        scores = conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0]
    finally:
        conn.close()
    assert scores == 1


def test_sqlite_transactor_persists_surface(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    a = _article("a")
    transactor = SqliteTransactor(db)
    transactor.persist_tags([_tag_result(a)], {str(a.id): a}, {"ai": "domain"}, NOW)
    transactor.persist_surface(
        a.id, TopicId("topic_a"),
        SurfaceRef(surface="slack", channel="D0X", message_id="ts-1", posted_at=NOW),
        Cadence.DAILY,
    )
    conn = connect(db)
    try:
        surfaces = conn.execute("SELECT COUNT(*) FROM surfaces").fetchone()[0]
    finally:
        conn.close()
    assert surfaces == 1
