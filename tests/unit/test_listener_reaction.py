from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from news_agent.core.types import (
    Article,
    ArticleId,
    Cadence,
    ContentHash,
    CorrectionKind,
    SourceId,
    SurfaceRef,
    TopicId,
)
from news_agent.notifier.listener import _REACTION_MAP, _resolve_reactions
from news_agent.storage.repository import (
    connect,
    init_db,
    save_surface,
    upsert_article,
)


def test_reaction_map_has_only_boost_and_demote():
    """SAVE and SKIP entries dropped per Apple-style spec."""
    assert set(_REACTION_MAP.values()) == {CorrectionKind.BOOST, CorrectionKind.DEMOTE}
    # +1/thumbsup → boost; -1/thumbsdown → demote (covers skin-tone variants).
    assert _REACTION_MAP["+1"] is CorrectionKind.BOOST
    assert _REACTION_MAP["thumbsup"] is CorrectionKind.BOOST
    assert _REACTION_MAP["-1"] is CorrectionKind.DEMOTE
    assert _REACTION_MAP["thumbsdown"] is CorrectionKind.DEMOTE
    # No SAVE/SKIP wired anymore.
    assert "bookmark" not in _REACTION_MAP
    assert "zzz" not in _REACTION_MAP


def test_resolve_reaction_returns_one_event_per_item_in_digest(tmp_path: Path):
    """A digest message holds N items; reaction must fan out to all N corrections.
    Otherwise SQLite's LIMIT 1 lookup attributes the boost/demote to an arbitrary
    item and corrupts the wrong source_prior."""
    db = tmp_path / "t.db"
    init_db(db)
    conn = connect(db)
    now = datetime.now(timezone.utc)
    article_a = Article(
        id=ArticleId("hn:aaaaaaaaaaaaaaaa"),
        source=SourceId("hn"),
        url="https://a", title="A", body="",
        content_hash=ContentHash("aaaaaaaaaaaaaaaa"),
        published_at=now, fetched_at=now,
    )
    article_b = Article(
        id=ArticleId("dou:bbbbbbbbbbbbbbbb"),
        source=SourceId("dou"),
        url="https://b", title="B", body="",
        content_hash=ContentHash("bbbbbbbbbbbbbbbb"),
        published_at=now, fetched_at=now,
    )
    try:
        upsert_article(conn, article_a)
        upsert_article(conn, article_b)
        # Same digest message → same channel/message_id for both items.
        for art, topic in [(article_a, "ai"), (article_b, "eng_leadership")]:
            save_surface(
                conn, art.id, TopicId(topic),
                SurfaceRef(
                    surface="slack", channel="D0X", message_id="1700000000.000100",
                    posted_at=now,
                ),
                Cadence.DAILY,
            )
        conn.commit()

        from news_agent.notifier.listener import _resolve_reactions

        events = _resolve_reactions(
            conn,
            channel="D0X",
            message_ts="1700000000.000100",
            kind=CorrectionKind.BOOST,
            user="U0X",
        )
    finally:
        conn.close()

    assert len(events) == 2
    targets = {(str(e.article), str(e.topic), str(e.source)) for e in events}
    assert ("hn:aaaaaaaaaaaaaaaa", "ai", "hn") in targets
    assert ("dou:bbbbbbbbbbbbbbbb", "eng_leadership", "dou") in targets
    assert all(e.kind is CorrectionKind.BOOST for e in events)


def test_resolve_reaction_returns_real_ids_from_surface(tmp_path: Path):
    """ts → (article, topic, source) via find_surface_target — no 'unknown' literals."""
    db = tmp_path / "t.db"
    init_db(db)
    conn = connect(db)
    try:
        article = Article(
            id=ArticleId("hn:abc123def456"),
            source=SourceId("hn"),
            url="https://news.ycombinator.com/item?id=1",
            title="T",
            body="b",
            content_hash=ContentHash("abc123def456"),
            published_at=datetime.now(timezone.utc),
            fetched_at=datetime.now(timezone.utc),
        )
        upsert_article(conn, article)
        save_surface(
            conn,
            article.id,
            TopicId("ai"),
            SurfaceRef(
                surface="slack", channel="D0X", message_id="1700000000.000100",
                posted_at=datetime.now(timezone.utc),
            ),
            Cadence.DAILY,
        )
        conn.commit()

        events = _resolve_reactions(
            conn,
            channel="D0X",
            message_ts="1700000000.000100",
            kind=CorrectionKind.BOOST,
            user="U0X",
        )
    finally:
        conn.close()

    assert len(events) == 1
    event = events[0]
    assert event.article == ArticleId("hn:abc123def456")
    assert event.topic == TopicId("ai")
    assert event.source == SourceId("hn")
    assert event.kind is CorrectionKind.BOOST
    assert event.user == "U0X"
    assert "unknown" not in {str(event.article), str(event.topic), str(event.source)}


def test_resolve_reactions_returns_empty_when_message_not_in_surfaces(tmp_path: Path):
    """Defensive: reactions on non-agent messages drop quietly (no events)."""
    db = tmp_path / "t.db"
    init_db(db)
    conn = connect(db)
    try:
        events = _resolve_reactions(
            conn,
            channel="D0X",
            message_ts="9999999999.000000",
            kind=CorrectionKind.BOOST,
            user="U0X",
        )
    finally:
        conn.close()
    assert events == []
