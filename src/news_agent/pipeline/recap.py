"""Weekly recap: query DB for the last N days; no fetch, no LLM."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol

from news_agent.core.types import Cadence, SurfaceRef
from news_agent.notifier.base import RecapPayload
from news_agent.pipeline.graph import PipelineDeps
from news_agent.storage.repository import (
    connect,
    query_skipped_high,
    query_top_scored,
    save_surface,
)


class _RecapNotifier(Protocol):
    def post_recap(self, payload: RecapPayload) -> SurfaceRef: ...


def run_weekly_recap(deps: PipelineDeps, *, window_days: int = 7) -> int:
    """Build and post a weekly recap. Returns total items surfaced."""
    if deps.notifier is None:
        deps.log("recap: skipped (no notifier configured)")
        return 0

    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    conn = connect(deps.db_path)
    try:
        top = query_top_scored(conn, since=since, limit=10)
        skipped = query_skipped_high(conn, since=since, min_score=0.85, limit=20)
    finally:
        conn.close()

    deps.log(f"recap: top={len(top)} skipped={len(skipped)} window={window_days}d")

    if not top and not skipped:
        deps.log("recap: nothing to post")
        return 0

    payload = RecapPayload(top_items=top, skipped_but_high=skipped, window_days=window_days)
    ref: SurfaceRef = deps.notifier.post_recap(payload)

    conn = connect(deps.db_path)
    try:
        for _article, sr in top + skipped:
            save_surface(conn, sr.article, sr.topic, ref, Cadence.WEEKLY)
        conn.commit()
    finally:
        conn.close()

    return len(top) + len(skipped)
