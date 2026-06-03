"""Interest sync: turn Safari reading-list activity into the taste profile.

Runs *outside* the discovery graph and its hash-dedup, because the strongest
signal — saving an item and reading it later — produces the same content hash
twice and would otherwise be dropped before it ever counts. Here we instead
detect the unread→read transition on the existing row and fold it into taste.

Only new items and freshly-read ones touch the profile, so steady-state runs
are cheap; the first run after install backfills taste from everything already
read in the reading list.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from news_agent.core.types import Article, Tag
from news_agent.learning.taste import interest_tags, reading_weight, taste_update
from news_agent.pipeline.scoring import tag_to_category
from news_agent.sources import make_source
from news_agent.sources.safari_reading_list import extract_read_at
from news_agent.storage.repository import (
    connect,
    get_article_read_state,
    load_article_tags,
    load_taste,
    save_taste,
    set_article_read_at,
    upsert_article,
)


@dataclass(frozen=True)
class InterestSummary:
    synced: int          # items fetched from interest sources
    newly_read: int      # unread→read transitions this run
    new_items: int       # never-seen items added this run
    taste_tags: int      # distinct tags in the profile after the update


def _interest_sources(deps) -> list:
    return [
        s for s in deps.sources_cfg.sources
        if getattr(s, "role", "discovery") == "interest" and s.enabled
    ]


def sync_interest(deps) -> InterestSummary:
    """Fetch interest sources, record read-state, update the taste profile.

    `deps` is the pipeline's PipelineDeps (sources_cfg, tags_cfg, db_path,
    tagger_model, log). Returns a summary for the caller to log/report.
    """
    sources = _interest_sources(deps)
    if not sources:
        return InterestSummary(0, 0, 0, 0)

    items: list[Article] = []
    for sc in sources:
        try:
            items.extend(make_source(sc).fetch())
        except Exception as exc:  # a missing plist / TCC denial shouldn't kill the run
            deps.log(f"interest: {sc.id} FAILED — {exc}")

    now = datetime.now(timezone.utc)
    conn = connect(deps.db_path)
    # (article, item_weight) for items that carry NEW signal this run.
    candidates: list[tuple[Article, float]] = []
    new_articles: list[Article] = []
    newly_read = 0
    try:
        for a in items:
            read_at = extract_read_at(a.raw.get("safari", {}) or {})
            is_read = read_at is not None
            exists, prev_read_at = get_article_read_state(conn, a.id)
            if not exists:
                upsert_article(conn, a)
                if is_read:
                    set_article_read_at(conn, a.id, read_at)
                new_articles.append(a)
                candidates.append((a, reading_weight(is_read=is_read)))
            elif is_read and prev_read_at is None:
                set_article_read_at(conn, a.id, read_at)
                newly_read += 1
                candidates.append((a, reading_weight(is_read=True)))
            # else: already read, or still-unread save we counted before — skip.
        conn.commit()

        new_tags = _tag_new_items(deps, new_articles, conn, now)

        # Oldest-first so recent reading dominates the EMA.
        candidates.sort(key=lambda c: c[0].published_at)
        cat_lookup = tag_to_category(deps.tags_cfg)
        weights = load_taste(conn)
        for article, item_weight in candidates:
            tags: frozenset[Tag] = new_tags.get(str(article.id)) or load_article_tags(conn, article.id)
            tags = interest_tags(tags, cat_lookup)
            if tags:
                weights = taste_update(weights, tags, item_weight)
        save_taste(conn, weights, now)
        conn.commit()
        taste_tags = len(weights)
    finally:
        conn.close()

    deps.log(
        f"interest: synced={len(items)} new={len(new_articles)} "
        f"newly_read={newly_read} taste_tags={taste_tags}"
    )
    return InterestSummary(
        synced=len(items),
        newly_read=newly_read,
        new_items=len(new_articles),
        taste_tags=taste_tags,
    )


def _tag_new_items(deps, new_articles, conn, now) -> dict[str, frozenset[Tag]]:
    """Tag never-seen interest items so taste has tags to learn from. Existing
    items already carry tags in the DB and are read from there."""
    if not new_articles:
        return {}

    from news_agent.llm._counting_client import CountingClient
    from news_agent.llm.tagger import _client as _tagger_client_factory
    from news_agent.llm.tagger import tag_articles
    from news_agent.pipeline.scoring import tag_to_category
    from news_agent.storage.repository import save_tag_result

    client = CountingClient(
        _tagger_client_factory(deps.tagger_model),
        model=deps.tagger_model, purpose="tag", db_path=deps.db_path,
    )
    results = tag_articles(
        new_articles, deps.tags_cfg, model=deps.tagger_model, client=client,
    )
    cat_lookup = tag_to_category(deps.tags_cfg)
    for r in results:
        save_tag_result(conn, r, now, cat_lookup)
    conn.commit()
    return {str(r.article): r.tags for r in results}
