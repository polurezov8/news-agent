"""Relative-time renderer for digest meta lines.

Pure: caller passes both `published_at` and `now` — no implicit clock reads.
Rules apply in order; first match wins.
"""

from __future__ import annotations

from datetime import datetime


def relative_time(published_at: datetime, *, now: datetime) -> str:
    elapsed_hours = (now - published_at).total_seconds() / 3600

    if elapsed_hours < 1:
        return "just now"
    if elapsed_hours < 12:
        return f"{int(elapsed_hours)}h ago"
    if elapsed_hours < 36:
        return "yesterday"

    elapsed_days = elapsed_hours / 24
    if elapsed_days < 7:
        return f"{int(elapsed_days)}d ago"

    return f"{published_at.strftime('%b')} {published_at.day}"
