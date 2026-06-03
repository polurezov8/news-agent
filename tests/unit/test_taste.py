from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from news_agent.core.types import Article, ArticleId, ContentHash, SourceId, Tag
from news_agent.learning.taste import (
    READ_WEIGHT,
    SAVED_WEIGHT,
    interest_tags,
    reading_weight,
    taste_adjustment,
    taste_update,
    top_taste,
    uncovered_interest_tags,
)
from news_agent.pipeline.scoring import compute_final, intrinsic_interest
from news_agent.sources.safari_reading_list import extract_read_at
from news_agent.storage.repository import (
    connect,
    get_article_read_state,
    init_db,
    load_taste,
    save_taste,
    set_article_read_at,
    upsert_article,
)


def _tags(*names: str) -> frozenset[Tag]:
    return frozenset(Tag(n) for n in names)


class TestReadingWeight:
    def test_read_beats_saved(self):
        assert reading_weight(is_read=True) == READ_WEIGHT
        assert reading_weight(is_read=False) == SAVED_WEIGHT
        assert READ_WEIGHT > SAVED_WEIGHT


class TestTasteUpdate:
    def test_first_touch_pulls_toward_item_weight(self):
        w = taste_update({}, _tags("swift"), 1.0, alpha=0.1)
        assert w["swift"] == 0.1  # (1-0.1)*0 + 0.1*1.0

    def test_repeated_reads_converge_up(self):
        w: dict[str, float] = {}
        for _ in range(100):
            w = taste_update(w, _tags("tca"), 1.0, alpha=0.1)
        assert w["tca"] > 0.99

    def test_does_not_mutate_input(self):
        original: dict[str, float] = {}
        taste_update(original, _tags("swift"), 1.0)
        assert original == {}

    def test_saved_unread_pulls_less_than_read(self):
        read = taste_update({}, _tags("x"), READ_WEIGHT, alpha=0.5)
        saved = taste_update({}, _tags("x"), SAVED_WEIGHT, alpha=0.5)
        assert read["x"] > saved["x"]


class TestInterestTags:
    _CATS = {
        "swift": "domain", "infra": "domain",
        "first_person": "quality", "data_driven": "quality",
        "listicle": "type", "news": "type", "opinion": "type", "essay": "type",
    }

    def test_drops_format_tags(self):
        kept = interest_tags(_tags("swift", "listicle", "news", "first_person"), self._CATS)
        assert kept == _tags("swift", "first_person")

    def test_drops_everything_when_all_format(self):
        assert interest_tags(_tags("news", "essay", "opinion"), self._CATS) == frozenset()

    def test_unknown_tag_dropped(self):
        assert interest_tags(_tags("mystery"), self._CATS) == frozenset()


class TestTasteAdjustment:
    def test_zero_without_tags(self):
        assert taste_adjustment(frozenset(), {"swift": 1.0}) == 0.0

    def test_zero_when_no_overlap(self):
        assert taste_adjustment(_tags("rust"), {"swift": 1.0}, k=0.3) == 0.0

    def test_mean_times_k(self):
        # tags swift(1.0) + tca(0.5) → mean 0.75 * k 0.4 = 0.3
        adj = taste_adjustment(_tags("swift", "tca"), {"swift": 1.0, "tca": 0.5}, k=0.4)
        assert abs(adj - 0.3) < 1e-9


class TestRecapHelpers:
    _CATS = {
        "infra": "domain", "security": "domain", "swift": "domain",
        "first_person": "quality", "data_driven": "quality",
    }

    def test_top_taste_orders_and_limits(self):
        w = {"a": 0.1, "b": 0.9, "c": 0.5}
        assert top_taste(w, n=2) == [("b", 0.9), ("c", 0.5)]

    def test_uncovered_excludes_covered_and_quality(self):
        w = {"infra": 0.7, "security": 0.5, "swift": 0.6, "first_person": 0.8}
        # swift is covered by a topic; first_person is quality (not a topic seed).
        out = uncovered_interest_tags(w, covered={"swift"}, category_lookup=self._CATS)
        assert out == ["infra", "security"]

    def test_uncovered_respects_floor(self):
        w = {"infra": 0.2}
        assert uncovered_interest_tags(w, covered=set(), category_lookup=self._CATS, floor=0.3) == []


class TestExtractReadAt:
    def test_read_item(self):
        dt = datetime(2026, 5, 12, tzinfo=timezone.utc)
        assert extract_read_at({"ReadingList": {"DateLastViewed": dt}}) == dt

    def test_unread_item(self):
        assert extract_read_at({"ReadingList": {"DateAdded": datetime.now(timezone.utc)}}) is None

    def test_naive_datetime_gets_utc(self):
        naive = datetime(2026, 5, 12, 9, 0)
        out = extract_read_at({"ReadingList": {"DateLastViewed": naive}})
        assert out is not None and out.tzinfo is timezone.utc

    def test_missing_reading_list(self):
        assert extract_read_at({}) is None


class TestGateVsFinal:
    """The de-silencing invariant: a substantive-but-decayed item clears the
    intrinsic gate even though its final score is far below the floor."""

    def test_decay_sinks_final_but_not_gate(self):
        substance, tag_adj = 0.6, 0.0
        decay, sw = 0.5, 0.8
        final = compute_final(substance, tag_adj, decay, sw)
        gate = intrinsic_interest(substance, tag_adj)
        assert final < 0.5      # would be dropped by the old final-based floor
        assert gate >= 0.5      # survives the new intrinsic gate

    def test_taste_is_added_outside_the_multiply(self):
        # taste must not be shrunk by decay/source_weight.
        no_taste = compute_final(0.4, 0.0, 0.5, 0.5, 0.0)
        with_taste = compute_final(0.4, 0.0, 0.5, 0.5, 0.2)
        assert abs((with_taste - no_taste) - 0.2) < 1e-9

    def test_taste_lifts_intrinsic_gate(self):
        base = intrinsic_interest(0.4, 0.0, 0.0)
        lifted = intrinsic_interest(0.4, 0.0, 0.15)
        assert abs((lifted - base) - 0.15) < 1e-9


class TestSyncInterest:
    """Exercises the real sync_interest orchestration (read-state transition,
    dedup-bypass, taste update) with zero LLM."""

    def _deps(self, db: Path):
        from types import SimpleNamespace

        from news_agent.config.schema import SourceConfig, SourcesConfig, TagsConfig
        return SimpleNamespace(
            sources_cfg=SourcesConfig(sources=[
                SourceConfig(id="rl", type="fake", topics=["ios"], weight=1.0, role="interest"),
            ]),
            tags_cfg=TagsConfig(tags={"domain": ["swift"], "type": ["news"]}),
            db_path=db,
            tagger_model="x",
            log=lambda *_a: None,
        )

    def _safari_article(self, *, read: bool):
        now = datetime.now(timezone.utc)
        item = {"ReadingList": {"DateAdded": now}}
        if read:
            item["ReadingList"]["DateLastViewed"] = datetime(2026, 5, 12, tzinfo=timezone.utc)
        return Article(
            id=ArticleId("safari_reading_list:x1"),
            source=SourceId("safari_reading_list"),
            url="https://ex/x1", title="Swift thing", body="b",
            content_hash=ContentHash("x1"), published_at=now, fetched_at=now,
            raw={"safari": item},
        )

    def test_unread_to_read_transition_updates_taste(self, tmp_path: Path, monkeypatch):
        from news_agent.pipeline import interest as interest_mod
        from news_agent.storage.repository import (
            save_tag_result,
        )
        from news_agent.core.types import TagResult

        db = tmp_path / "t.db"
        init_db(db)
        # Pre-seed the article UNREAD, already tagged (swift=domain, news=type).
        conn = connect(db)
        a = self._safari_article(read=False)
        upsert_article(conn, a)
        save_tag_result(
            conn, TagResult(article=a.id, tags=_tags("swift", "news"), confidence=1.0, model="x"),
            datetime.now(timezone.utc), {"swift": "domain", "news": "type"},
        )
        conn.commit()
        conn.close()

        # Fake source now reports the same item as READ.
        read_item = self._safari_article(read=True)
        monkeypatch.setattr(interest_mod, "make_source", lambda sc: SimpleNamespaceSource([read_item]))

        summary = interest_mod.sync_interest(self._deps(db))
        assert summary.newly_read == 1
        assert summary.new_items == 0

        conn = connect(db)
        try:
            exists, read_at = get_article_read_state(conn, a.id)
            assert exists and read_at is not None          # read-state synced
            taste = load_taste(conn)
            assert taste.get("swift", 0) > 0               # domain tag learned
            assert "news" not in taste                     # format tag excluded
        finally:
            conn.close()

    def test_already_read_is_no_op(self, tmp_path: Path, monkeypatch):
        from news_agent.pipeline import interest as interest_mod

        db = tmp_path / "t.db"
        init_db(db)
        read_item = self._safari_article(read=True)
        # First sync (new item) — skip tagging to stay LLM-free.
        monkeypatch.setattr(interest_mod, "make_source", lambda sc: SimpleNamespaceSource([read_item]))
        monkeypatch.setattr(interest_mod, "_tag_new_items", lambda *a, **k: {})
        first = interest_mod.sync_interest(self._deps(db))
        assert first.new_items == 1
        # Second sync — same item, already read → no new signal.
        second = interest_mod.sync_interest(self._deps(db))
        assert second.newly_read == 0 and second.new_items == 0


class SimpleNamespaceSource:
    """Minimal Source stand-in: returns a fixed list from fetch()."""

    def __init__(self, items):
        self._items = items

    def fetch(self):
        return self._items


class TestReadStatePersistence:
    def _article(self) -> Article:
        now = datetime.now(timezone.utc)
        return Article(
            id=ArticleId("safari_reading_list:abc"),
            source=SourceId("safari_reading_list"),
            url="https://x.com/a",
            title="t",
            body="b",
            content_hash=ContentHash("abc"),
            published_at=now,
            fetched_at=now,
        )

    def test_read_state_transition(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        conn = connect(db)
        try:
            a = self._article()
            upsert_article(conn, a)
            conn.commit()
            exists, read_at = get_article_read_state(conn, a.id)
            assert exists and read_at is None

            when = datetime(2026, 5, 12, tzinfo=timezone.utc)
            set_article_read_at(conn, a.id, when)
            conn.commit()
            exists, read_at = get_article_read_state(conn, a.id)
            assert exists and read_at == when.isoformat()
        finally:
            conn.close()

    def test_taste_round_trip(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        conn = connect(db)
        try:
            now = datetime.now(timezone.utc)
            save_taste(conn, {"swift": 0.8, "tca": 0.4}, now)
            conn.commit()
            assert load_taste(conn) == {"swift": 0.8, "tca": 0.4}
            # overwrite is idempotent upsert
            save_taste(conn, {"swift": 0.9}, now)
            conn.commit()
            assert load_taste(conn)["swift"] == 0.9
        finally:
            conn.close()
