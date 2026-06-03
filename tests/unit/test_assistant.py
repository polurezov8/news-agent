from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from news_agent.application import AppContext, adjust_source_prior
from news_agent.config.schema import (
    PriorsConfig,
    SourceConfig,
    SourcesConfig,
    TagsConfig,
    TopicsConfig,
)
from news_agent.core.types import Article, ArticleId, ContentHash, SourceId
from news_agent.notifier.assistant import _fmt_articles, _to_slack_mrkdwn, build_tools
from news_agent.storage.repository import (
    connect,
    get_source_prior,
    init_db,
    recent_reading_list,
    search_articles,
    set_article_read_at,
    upsert_article,
)


def _ctx(tmp_path: Path) -> AppContext:
    db = tmp_path / "t.db"
    init_db(db)
    return AppContext(
        sources_cfg=SourcesConfig(sources=[
            SourceConfig(id="pointfree", type="rss", topics=["ios"], weight=0.9),
        ]),
        topics_cfg=TopicsConfig(topics={}),
        tags_cfg=TagsConfig(tags={}),
        priors_cfg=PriorsConfig(priors=[]),
        db_path=db,
        tagger_model="t",
        scorer_model="s",
        budget_usd=5.0,
    )


def _seed(db: Path) -> None:
    now = datetime.now(timezone.utc)
    conn = connect(db)
    try:
        for i, title in enumerate(["Swift macros deep dive", "A note on Rust", "TCA testing"]):
            upsert_article(conn, Article(
                id=ArticleId(f"rss:{i}"),
                source=SourceId("pointfree"),
                url=f"https://ex/{i}",
                title=title,
                body="body about " + title,
                content_hash=ContentHash(str(i)),
                published_at=now,
                fetched_at=now,
            ))
        conn.commit()
    finally:
        conn.close()


class TestSearchArticles:
    def test_keyword_match(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        _seed(db)
        conn = connect(db)
        try:
            rows = search_articles(conn, query="Swift", limit=10)
            titles = [a.title for a, _ in rows]
            assert "Swift macros deep dive" in titles
            assert "A note on Rust" not in titles
        finally:
            conn.close()

    def test_no_match_returns_empty(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        _seed(db)
        conn = connect(db)
        try:
            assert search_articles(conn, query="kubernetes", limit=10) == []
        finally:
            conn.close()


class TestReadingList:
    def _seed_interest(self, db: Path) -> None:
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        conn = connect(db)
        try:
            for i in range(3):
                upsert_article(conn, Article(
                    id=ArticleId(f"safari_reading_list:{i}"),
                    source=SourceId("safari_reading_list"),
                    url=f"https://ex/{i}", title=f"Item {i}", body="b",
                    content_hash=ContentHash(str(i)),
                    published_at=now - timedelta(days=i), fetched_at=now,
                ))
            # item 2 was read later than anything was saved → newest activity.
            set_article_read_at(conn, ArticleId("safari_reading_list:2"), now + timedelta(days=1))
            conn.commit()
        finally:
            conn.close()

    def test_returns_interest_articles_with_read_state(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        self._seed_interest(db)
        conn = connect(db)
        try:
            rows = recent_reading_list(conn, source_ids=["safari_reading_list"], limit=5)
        finally:
            conn.close()
        assert len(rows) == 3
        # item 2 is read → sorts first by COALESCE(read_at, published_at)
        first_article, first_read = rows[0]
        assert first_article.id == "safari_reading_list:2"
        assert first_read is not None

    def test_empty_source_ids_returns_nothing(self, tmp_path: Path):
        db = tmp_path / "t.db"
        init_db(db)
        conn = connect(db)
        try:
            assert recent_reading_list(conn, source_ids=[], limit=5) == []
        finally:
            conn.close()


class TestAdjustSourcePrior:
    def test_boost_all_declared_topics(self, tmp_path: Path):
        ctx = _ctx(tmp_path)
        changes = adjust_source_prior("pointfree", "boost", ctx=ctx, log=lambda _m: None)
        assert changes == [("ios", 0.5, 0.55)]  # 0.5 base + BOOST(+1.0 * lr 0.05)
        conn = connect(ctx.db_path)
        try:
            assert get_source_prior(conn, SourceId("pointfree"), "ios") == 0.55  # type: ignore[arg-type]
        finally:
            conn.close()

    def test_demote_moves_down(self, tmp_path: Path):
        ctx = _ctx(tmp_path)
        changes = adjust_source_prior("pointfree", "demote", topic="ios", ctx=ctx, log=lambda _m: None)
        _topic, before, after = changes[0]
        assert after < before

    def test_unknown_source_raises(self, tmp_path: Path):
        ctx = _ctx(tmp_path)
        import pytest

        from news_agent.application import ApplicationError
        with pytest.raises(ApplicationError):
            adjust_source_prior("nope", "boost", ctx=ctx, log=lambda _m: None)


class TestAssistantTooling:
    def test_build_tools_exposes_expected(self):
        names = {t.name for t in build_tools()}
        assert names == {
            "search_news", "recent_picks", "reading_list", "my_taste",
            "cost_status", "run_now", "adjust_source",
        }

    def test_fmt_articles_empty(self):
        assert _fmt_articles([]) == "No matches."


class TestSlackMrkdwn:
    def test_double_asterisk_to_single(self):
        assert _to_slack_mrkdwn("- **252** fetched") == "- *252* fetched"

    def test_multiple_bolds(self):
        assert _to_slack_mrkdwn("**a** and **b**") == "*a* and *b*"

    def test_header_to_bold(self):
        assert _to_slack_mrkdwn("### Strong signals") == "*Strong signals*"

    def test_plain_text_untouched(self):
        assert _to_slack_mrkdwn("just text, one * star") == "just text, one * star"

    def test_slack_link_untouched(self):
        link = "<https://x|Title> is *already* slack"
        assert _to_slack_mrkdwn(link) == link

    def test_fmt_articles_renders_slack_link(self):
        now = datetime.now(timezone.utc)
        a = Article(
            id=ArticleId("rss:1"), source=SourceId("pointfree"),
            url="https://ex/1", title="Swift macros", body="b",
            content_hash=ContentHash("1"), published_at=now, fetched_at=now,
        )
        out = _fmt_articles([(a, 0.71)])
        assert "<https://ex/1|Swift macros>" in out and "0.71" in out
