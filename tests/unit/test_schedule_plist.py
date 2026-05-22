from __future__ import annotations

import pytest

from news_agent.cli.schedule import LABEL_PREFIX, generate_plist
from news_agent.core.types import Cadence


def _plist(cadence: Cadence) -> str:
    return generate_plist(
        cadence,
        bin_path="/path/to/news-agent",
        working_dir="/proj",
        log_dir="/logs",
    )


def test_daily_uses_calendar_interval_at_10_00():
    p = _plist(Cadence.DAILY)
    assert "<key>StartCalendarInterval</key>" in p
    assert "<key>Hour</key><integer>10</integer>" in p
    assert "<key>Minute</key><integer>0</integer>" in p
    assert "<key>Weekday</key>" not in p


def test_priority_uses_start_interval_one_hour():
    p = _plist(Cadence.PRIORITY)
    assert "<key>StartInterval</key>" in p
    assert "<integer>3600</integer>" in p
    assert "<key>StartCalendarInterval</key>" not in p


def test_weekly_uses_calendar_interval_sunday_10_00():
    p = _plist(Cadence.WEEKLY)
    assert "<key>StartCalendarInterval</key>" in p
    assert "<key>Weekday</key><integer>0</integer>" in p   # 0 = Sunday in launchd
    assert "<key>Hour</key><integer>10</integer>" in p


def test_label_includes_cadence():
    assert f"{LABEL_PREFIX}.daily" in _plist(Cadence.DAILY)
    assert f"{LABEL_PREFIX}.priority" in _plist(Cadence.PRIORITY)
    assert f"{LABEL_PREFIX}.weekly" in _plist(Cadence.WEEKLY)


def test_program_arguments_use_cadence_flag():
    p = _plist(Cadence.PRIORITY)
    assert "<string>/path/to/news-agent</string>" in p
    assert "<string>--cadence</string>" in p
    assert "<string>priority</string>" in p


def test_logs_path_per_cadence():
    p = _plist(Cadence.DAILY)
    assert "<string>/logs/daily.log</string>" in p
    assert "<string>/logs/daily.err</string>" in p


def test_on_demand_is_unschedulable():
    with pytest.raises(ValueError):
        generate_plist(Cadence.ON_DEMAND, bin_path="x", working_dir="x", log_dir="x")


def test_listener_plist_runs_continuously_with_keepalive():
    """Reaction listener runs as a daemon — RunAtLoad + KeepAlive, no calendar/interval schedule."""
    from news_agent.cli.schedule import LISTENER_LABEL, generate_listener_plist

    p = generate_listener_plist(
        bin_path="/path/to/news-agent",
        working_dir="/proj",
        log_dir="/logs",
    )
    assert LISTENER_LABEL in p
    assert "<key>RunAtLoad</key>" in p
    assert "<true/>" in p
    assert "<key>KeepAlive</key>" in p
    # listener invokes `news-agent slack` (Socket Mode listener)
    assert "<string>slack</string>" in p
    # not a cron — no calendar or interval schedule
    assert "<key>StartCalendarInterval</key>" not in p
    assert "<key>StartInterval</key>" not in p
    # logs per the listener bucket
    assert "<string>/logs/listener.log</string>" in p
    assert "<string>/logs/listener.err</string>" in p
