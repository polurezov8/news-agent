from __future__ import annotations

from datetime import datetime, timedelta, timezone

from news_agent.notifier._time import relative_time

_NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)


def _ago(*, hours: float = 0, days: float = 0) -> datetime:
    return _NOW - timedelta(hours=hours, days=days)


def test_just_now_under_one_hour():
    assert relative_time(_ago(hours=0), now=_NOW) == "just now"
    assert relative_time(_ago(hours=0.5), now=_NOW) == "just now"
    assert relative_time(_ago(hours=0.99), now=_NOW) == "just now"


def test_hours_between_1_and_12():
    assert relative_time(_ago(hours=1), now=_NOW) == "1h ago"
    assert relative_time(_ago(hours=4), now=_NOW) == "4h ago"
    assert relative_time(_ago(hours=11.5), now=_NOW) == "11h ago"


def test_yesterday_between_12h_and_36h():
    assert relative_time(_ago(hours=12), now=_NOW) == "yesterday"
    assert relative_time(_ago(hours=20), now=_NOW) == "yesterday"
    assert relative_time(_ago(hours=35.9), now=_NOW) == "yesterday"


def test_days_between_36h_and_7d():
    assert relative_time(_ago(hours=36), now=_NOW) == "1d ago"
    assert relative_time(_ago(days=2), now=_NOW) == "2d ago"
    assert relative_time(_ago(days=6.9), now=_NOW) == "6d ago"


def test_absolute_date_at_seven_days_or_more():
    week = _ago(days=7)
    assert relative_time(week, now=_NOW) == week.strftime("%b ").replace(" 0", " ") + str(week.day)
    older = _ago(days=30)
    assert relative_time(older, now=_NOW) == older.strftime("%b ").replace(" 0", " ") + str(older.day)


def test_future_published_at_treated_as_just_now():
    """Defensive: if published_at is slightly in the future (clock skew, timezone bug),
    render as 'just now' rather than negative-hour weirdness."""
    assert relative_time(_NOW + timedelta(minutes=5), now=_NOW) == "just now"


def test_pure_function_no_default_now():
    """now is required — function is pure, no implicit clock reads."""
    import inspect

    sig = inspect.signature(relative_time)
    assert sig.parameters["now"].default is inspect.Parameter.empty
