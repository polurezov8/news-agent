from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from news_agent.core.types import (
    ArticleId,
    Cadence,
    CorrectionEvent,
    CorrectionKind,
    SourceId,
    SurfaceRef,
    TopicId,
)
from news_agent.storage.repository import (
    connect,
    find_surface_target,
    get_source_prior,
    init_db,
    load_priors_dict,
    save_correction,
    save_surface,
    upsert_article,
    upsert_source_prior,
)
from tests.unit.test_storage_crud import _article


def _make_correction(
    *,
    article_id: str = "hn:abc123def456",
    topic_id: str = "topic_a",
    kind: CorrectionKind = CorrectionKind.BOOST,
) -> CorrectionEvent:
    return CorrectionEvent(
        article=ArticleId(article_id),
        topic=TopicId(topic_id),
        source=SourceId(article_id.split(":")[0]),
        kind=kind,
        surface=SurfaceRef(
            surface="slack", channel="D0X", message_id="1.0",
            posted_at=datetime.now(timezone.utc),
        ),
        user="U0X",
        at=datetime.now(timezone.utc),
    )


class TestSaveCorrection:
    def test_persists_row(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        article = _article()
        conn = connect(db)
        try:
            upsert_article(conn, article)
            save_correction(conn, _make_correction(article_id=str(article.id)))
            conn.commit()
            row = conn.execute(
                "SELECT kind, source_id, topic_id, user FROM corrections"
            ).fetchone()
            assert row["kind"] == "boost"
            assert row["source_id"] == "hn"
            assert row["topic_id"] == "topic_a"
            assert row["user"] == "U0X"
        finally:
            conn.close()


class TestUpsertSourcePrior:
    def test_insert(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        conn = connect(db)
        try:
            upsert_source_prior(
                conn, SourceId("hn"), TopicId("ai"), 0.7, datetime.now(timezone.utc),
            )
            conn.commit()
            assert get_source_prior(conn, SourceId("hn"), TopicId("ai")) == 0.7
        finally:
            conn.close()

    def test_update_existing(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        conn = connect(db)
        try:
            upsert_source_prior(
                conn, SourceId("hn"), TopicId("ai"), 0.7, datetime.now(timezone.utc),
            )
            upsert_source_prior(
                conn, SourceId("hn"), TopicId("ai"), 0.85, datetime.now(timezone.utc),
            )
            conn.commit()
            assert get_source_prior(conn, SourceId("hn"), TopicId("ai")) == 0.85
            count = conn.execute("SELECT COUNT(*) FROM source_priors").fetchone()[0]
            assert count == 1
        finally:
            conn.close()


class TestFindSurfaceTarget:
    def test_returns_none_when_missing(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        conn = connect(db)
        try:
            assert find_surface_target(conn, "D0X", "9.9") is None
        finally:
            conn.close()

    def test_returns_article_and_topic(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        article = _article()
        ref = SurfaceRef(
            surface="slack", channel="D0X", message_id="1.5",
            posted_at=datetime.now(timezone.utc),
        )
        conn = connect(db)
        try:
            upsert_article(conn, article)
            save_surface(conn, article.id, TopicId("topic_a"), ref, Cadence.DAILY)
            conn.commit()
            result = find_surface_target(conn, "D0X", "1.5")
            assert result is not None
            assert result == (article.id, TopicId("topic_a"))
        finally:
            conn.close()


class TestLoadPriorsDict:
    def test_empty(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        conn = connect(db)
        try:
            assert load_priors_dict(conn) == {}
        finally:
            conn.close()

    def test_returns_all(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        now = datetime.now(timezone.utc)
        conn = connect(db)
        try:
            upsert_source_prior(conn, SourceId("hn"), TopicId("ai"), 0.7, now)
            upsert_source_prior(conn, SourceId("hn"), TopicId("ios"), 0.5, now)
            conn.commit()
            d = load_priors_dict(conn)
            assert d == {("hn", "ai"): 0.7, ("hn", "ios"): 0.5}
        finally:
            conn.close()
