from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from news_agent.core.types import (
    Article,
    ArticleId,
    Cadence,
    ContentHash,
    ScoreResult,
    SourceId,
    SurfaceRef,
    TopicId,
)
from news_agent.notifier.base import RecapPayload
from news_agent.pipeline.graph import PipelineDeps
from news_agent.pipeline.recap import run_weekly_recap
from news_agent.storage.repository import (
    connect,
    init_db,
    save_score,
    save_surface,
    upsert_article,
)


@dataclass
class _RecordingNotifier:
    recaps: list[RecapPayload] = field(default_factory=list)

    def post_digest(self, payload):
        raise AssertionError("recap should not call post_digest")

    def dm_priority(self, payload):
        raise AssertionError("recap should not call dm_priority")

    def post_recap(self, payload: RecapPayload) -> SurfaceRef:
        self.recaps.append(payload)
        return SurfaceRef(
            surface="slack", channel="D0X", message_id="r-1",
            posted_at=datetime.now(timezone.utc),
        )

    def handle_correction(self, ev):
        pass


def _seed(conn, *, article_id: str, fetched_offset_days: float, final: float) -> tuple[Article, ScoreResult]:
    now = datetime.now(timezone.utc)
    fetched_at = now - timedelta(days=fetched_offset_days)
    article = Article(
        id=ArticleId(article_id),
        source=SourceId(article_id.split(":")[0]),
        url=f"https://example.com/{article_id}",
        title=f"Article {article_id}",
        body="body",
        content_hash=ContentHash(article_id.split(":")[1]),
        published_at=fetched_at,
        fetched_at=fetched_at,
    )
    score = ScoreResult(
        article=article.id,
        topic=TopicId("topic_a"),
        substance=final,
        tag_adj=0.0,
        decay=1.0,
        source_weight=1.0,
        final=final,
    )
    upsert_article(conn, article)
    save_score(conn, score, fetched_at)
    return article, score


def _deps(db_path: Path, notifier) -> PipelineDeps:
    # Minimal-config deps; recap doesn't touch sources/topics/tags/priors.
    from news_agent.config.schema import (
        PriorsConfig, SourcesConfig, TagsConfig, TopicsConfig,
    )
    from news_agent.pipeline.transactor import SqliteTransactor

    return PipelineDeps(
        sources_cfg=SourcesConfig(sources=[]),
        topics_cfg=TopicsConfig(topics={}),
        tags_cfg=TagsConfig(tags={}),
        priors_cfg=PriorsConfig(priors=[]),
        db_path=db_path,
        notifier=notifier,
        transactor=SqliteTransactor(db_path),
        cadence=Cadence.WEEKLY,
        log=lambda msg: None,
    )


def test_recap_empty_db_posts_nothing(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    notifier = _RecordingNotifier()
    n = run_weekly_recap(_deps(db, notifier))
    assert n == 0
    assert notifier.recaps == []


def test_recap_picks_top_items_within_window(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    conn = connect(db)
    try:
        _seed(conn, article_id="hn:aaa111111111aaaa", fetched_offset_days=1, final=0.9)
        _seed(conn, article_id="hn:bbb222222222bbbb", fetched_offset_days=2, final=0.5)
        _seed(conn, article_id="hn:ccc333333333cccc", fetched_offset_days=30, final=0.95)  # outside window
        conn.commit()
    finally:
        conn.close()

    notifier = _RecordingNotifier()
    n = run_weekly_recap(_deps(db, notifier), window_days=7)
    assert n > 0
    assert len(notifier.recaps) == 1
    payload = notifier.recaps[0]
    titles = {a.title for a, _ in payload.top_items}
    assert "Article hn:ccc333333333cccc" not in titles    # too old
    assert "Article hn:aaa111111111aaaa" in titles


def test_recap_skipped_high_excludes_already_surfaced(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    conn = connect(db)
    try:
        _, sr_posted = _seed(conn, article_id="hn:1111111111111111", fetched_offset_days=1, final=0.92)
        _, _ = _seed(conn, article_id="hn:2222222222222222", fetched_offset_days=2, final=0.91)
        # Mark posted one as already surfaced (e.g., went out in daily digest)
        save_surface(
            conn, sr_posted.article, sr_posted.topic,
            SurfaceRef(surface="slack", channel="D0X", message_id="d-1", posted_at=datetime.now(timezone.utc)),
            Cadence.DAILY,
        )
        conn.commit()
    finally:
        conn.close()

    notifier = _RecordingNotifier()
    run_weekly_recap(_deps(db, notifier), window_days=7)
    skipped_titles = {a.title for a, _ in notifier.recaps[0].skipped_but_high}
    assert "Article hn:1111111111111111" not in skipped_titles    # already posted
    assert "Article hn:2222222222222222" in skipped_titles


def test_recap_dry_run_no_notifier(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    deps = _deps(db, notifier=None)
    n = run_weekly_recap(deps)
    assert n == 0
