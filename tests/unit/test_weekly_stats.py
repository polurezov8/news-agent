from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from news_agent.core.types import (
    Article,
    ArticleId,
    Cadence,
    ContentHash,
    CorrectionEvent,
    CorrectionKind,
    SourceId,
    SurfaceRef,
    TopicId,
)
from news_agent.storage.repository import (
    connect,
    count_corrections_by_kind,
    count_distinct_run_days,
    count_surfaces_in_window,
    init_db,
    save_correction,
    save_surface,
    top_sources_in_window,
    upsert_article,
)


def _seed_article(conn, *, article_id: str, source: str = "hn") -> Article:
    now = datetime.now(timezone.utc)
    article = Article(
        id=ArticleId(article_id),
        source=SourceId(source),
        url=f"https://example.com/{article_id}",
        title=f"T {article_id}",
        body="b",
        content_hash=ContentHash(article_id.split(":")[1]),
        published_at=now,
        fetched_at=now,
    )
    upsert_article(conn, article)
    return article


def _seed_surface(conn, *, article_id: str, days_ago: float, cadence: Cadence) -> None:
    posted_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    save_surface(
        conn,
        ArticleId(article_id),
        TopicId("topic_a"),
        SurfaceRef(
            surface="slack",
            channel="D0X",
            message_id=f"m-{article_id}-{days_ago}",
            posted_at=posted_at,
        ),
        cadence,
    )


def test_count_distinct_run_days_counts_unique_days(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    conn = connect(db)
    try:
        a = _seed_article(conn, article_id="hn:aaa111111111aaaa")
        b = _seed_article(conn, article_id="hn:bbb222222222bbbb")
        c = _seed_article(conn, article_id="hn:ccc333333333cccc")
        # 3 surfaces on day1 + 1 on day2 + 1 on day5 = 3 distinct days
        _seed_surface(conn, article_id=str(a.id), days_ago=1, cadence=Cadence.DAILY)
        _seed_surface(conn, article_id=str(b.id), days_ago=1, cadence=Cadence.DAILY)
        _seed_surface(conn, article_id=str(c.id), days_ago=1, cadence=Cadence.DAILY)
        _seed_surface(conn, article_id=str(a.id), days_ago=2, cadence=Cadence.DAILY)
        _seed_surface(conn, article_id=str(b.id), days_ago=5, cadence=Cadence.DAILY)
        # One priority surface should NOT count
        _seed_surface(conn, article_id=str(c.id), days_ago=1, cadence=Cadence.PRIORITY)
        conn.commit()

        since = datetime.now(timezone.utc) - timedelta(days=7)
        assert count_distinct_run_days(conn, since=since, cadence=Cadence.DAILY) == 3
    finally:
        conn.close()


def test_count_surfaces_in_window_only_within_range(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    conn = connect(db)
    try:
        a = _seed_article(conn, article_id="hn:aaa111111111aaaa")
        _seed_surface(conn, article_id=str(a.id), days_ago=1, cadence=Cadence.DAILY)
        _seed_surface(conn, article_id=str(a.id), days_ago=3, cadence=Cadence.DAILY)
        _seed_surface(conn, article_id=str(a.id), days_ago=10, cadence=Cadence.DAILY)  # outside
        conn.commit()

        since = datetime.now(timezone.utc) - timedelta(days=7)
        assert count_surfaces_in_window(conn, since=since, cadence=Cadence.DAILY) == 2
    finally:
        conn.close()


def test_count_corrections_by_kind(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    conn = connect(db)
    try:
        a = _seed_article(conn, article_id="hn:aaa111111111aaaa")
        b = _seed_article(conn, article_id="hn:bbb222222222bbbb")
        now = datetime.now(timezone.utc)
        for kind, days in [
            (CorrectionKind.BOOST, 1),
            (CorrectionKind.BOOST, 2),
            (CorrectionKind.DEMOTE, 3),
            (CorrectionKind.BOOST, 10),  # outside window
        ]:
            save_correction(
                conn,
                CorrectionEvent(
                    article=a.id if kind is CorrectionKind.BOOST else b.id,
                    topic=TopicId("topic_a"),
                    source=SourceId("hn"),
                    kind=kind,
                    surface=SurfaceRef(
                        surface="slack", channel="D0X",
                        message_id=f"m-{kind}-{days}",
                        posted_at=now - timedelta(days=days),
                    ),
                    user="U0X",
                    at=now - timedelta(days=days),
                ),
            )
        conn.commit()

        since = now - timedelta(days=7)
        assert count_corrections_by_kind(conn, since=since, kind=CorrectionKind.BOOST) == 2
        assert count_corrections_by_kind(conn, since=since, kind=CorrectionKind.DEMOTE) == 1
    finally:
        conn.close()


def test_top_sources_in_window_returns_ordered_pairs(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    conn = connect(db)
    try:
        # Source distribution within window: hn=3, dou=2, pf=1, lobsters=outside
        for src, n in [("hn", 3), ("dou", 2), ("pf", 1)]:
            for i in range(n):
                aid = f"{src}:{src*4}{i:012d}"
                _seed_article(conn, article_id=aid, source=src)
                _seed_surface(conn, article_id=aid, days_ago=2, cadence=Cadence.DAILY)
        # Out-of-window surface for lobsters
        a = _seed_article(conn, article_id="lobsters:000000000000zzzz", source="lobsters")
        _seed_surface(conn, article_id=str(a.id), days_ago=15, cadence=Cadence.DAILY)
        conn.commit()

        since = datetime.now(timezone.utc) - timedelta(days=7)
        top = top_sources_in_window(conn, since=since, cadence=Cadence.DAILY, limit=3)
        assert top == [("hn", 3), ("dou", 2), ("pf", 1)]
    finally:
        conn.close()
