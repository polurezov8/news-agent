from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

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
from news_agent.storage.repository import (
    article_exists_by_hash,
    connect,
    get_source_prior,
    init_db,
    save_score,
    save_surface,
    save_tag_result,
    upsert_article,
)


def _article(aid: str = "hn:abc123def456", body: str = "body") -> Article:
    now = datetime.now(timezone.utc)
    return Article(
        id=ArticleId(aid),
        source=SourceId(aid.split(":")[0]),
        url="https://example.com",
        title="Test",
        body=body,
        content_hash=ContentHash(aid.split(":")[1]),
        published_at=now,
        fetched_at=now,
    )


class TestDedup:
    def test_article_exists_returns_false_for_missing(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        conn = connect(db)
        try:
            assert article_exists_by_hash(conn, ContentHash("missing")) is False
        finally:
            conn.close()

    def test_upsert_then_exists(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        article = _article()
        conn = connect(db)
        try:
            upsert_article(conn, article)
            conn.commit()
            assert article_exists_by_hash(conn, article.content_hash) is True
        finally:
            conn.close()

    def test_upsert_idempotent(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        article = _article()
        conn = connect(db)
        try:
            upsert_article(conn, article)
            upsert_article(conn, article)
            conn.commit()
            n = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            assert n == 1
        finally:
            conn.close()


class TestSaveTagResult:
    def test_writes_one_row_per_tag_with_category(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        article = _article()
        result = TagResult(
            article=article.id,
            tags=frozenset([Tag("essay"), Tag("hands_on")]),
            confidence=0.9,
            model="claude-haiku",
        )
        conn = connect(db)
        try:
            upsert_article(conn, article)
            save_tag_result(
                conn, result, datetime.now(timezone.utc),
                category_lookup={"essay": "type", "hands_on": "quality"},
            )
            conn.commit()
            rows = conn.execute(
                "SELECT tag, category FROM tags ORDER BY tag"
            ).fetchall()
            assert [(r["tag"], r["category"]) for r in rows] == [
                ("essay", "type"),
                ("hands_on", "quality"),
            ]
        finally:
            conn.close()

    def test_unknown_category_when_lookup_empty(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        article = _article()
        result = TagResult(
            article=article.id,
            tags=frozenset([Tag("mystery")]),
            confidence=1.0,
            model="m",
        )
        conn = connect(db)
        try:
            upsert_article(conn, article)
            save_tag_result(conn, result, datetime.now(timezone.utc))
            conn.commit()
            row = conn.execute("SELECT category FROM tags").fetchone()
            assert row["category"] == "unknown"
        finally:
            conn.close()


class TestSaveScore:
    def test_writes_score(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        article = _article()
        sr = ScoreResult(
            article=article.id,
            topic=TopicId("topic_a"),
            substance=0.8,
            tag_adj=0.1,
            decay=0.9,
            source_weight=0.7,
            final=0.567,
        )
        conn = connect(db)
        try:
            upsert_article(conn, article)
            save_score(conn, sr, datetime.now(timezone.utc))
            conn.commit()
            row = conn.execute(
                "SELECT substance, final_score FROM scores"
            ).fetchone()
            assert row["substance"] == 0.8
            assert row["final_score"] == 0.567
        finally:
            conn.close()


class TestSaveSurface:
    def test_writes_surface_ref(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        article = _article()
        ref = SurfaceRef(
            surface="slack",
            channel="D0X",
            message_id="1234.5678",
            posted_at=datetime.now(timezone.utc),
        )
        conn = connect(db)
        try:
            upsert_article(conn, article)
            save_surface(conn, article.id, TopicId("topic_a"), ref, Cadence.DAILY)
            conn.commit()
            row = conn.execute(
                "SELECT cadence, channel, message_id FROM surfaces"
            ).fetchone()
            assert row["cadence"] == "daily"
            assert row["channel"] == "D0X"
            assert row["message_id"] == "1234.5678"
        finally:
            conn.close()


class TestHasSurface:
    def test_returns_false_when_missing(self, tmp_path: Path):
        from news_agent.storage.repository import has_surface

        db = tmp_path / "t.db"
        init_db(db)
        conn = connect(db)
        try:
            assert has_surface(
                conn, ArticleId("hn:x"), TopicId("topic_a"), Cadence.PRIORITY,
            ) is False
        finally:
            conn.close()

    def test_returns_true_after_save(self, tmp_path: Path):
        from news_agent.storage.repository import has_surface

        db = tmp_path / "t.db"
        init_db(db)
        article = _article()
        ref = SurfaceRef(
            surface="slack", channel="D0X", message_id="x.y",
            posted_at=datetime.now(timezone.utc),
        )
        conn = connect(db)
        try:
            upsert_article(conn, article)
            save_surface(conn, article.id, TopicId("topic_a"), ref, Cadence.PRIORITY)
            conn.commit()
            assert has_surface(
                conn, article.id, TopicId("topic_a"), Cadence.PRIORITY,
            ) is True
            # Different cadence: not surfaced.
            assert has_surface(
                conn, article.id, TopicId("topic_a"), Cadence.DAILY,
            ) is False
        finally:
            conn.close()


class TestSourcePrior:
    def test_missing_returns_none(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        conn = connect(db)
        try:
            assert get_source_prior(conn, SourceId("hn"), TopicId("ai")) is None
        finally:
            conn.close()

    def test_returns_stored_weight(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        conn = connect(db)
        try:
            conn.execute(
                "INSERT INTO source_priors (source_id, topic_id, weight, updated_at) VALUES (?, ?, ?, ?)",
                ("hn", "ai", 0.85, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            assert get_source_prior(conn, SourceId("hn"), TopicId("ai")) == 0.85
        finally:
            conn.close()
