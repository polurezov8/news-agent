"""Smoke test: schema.sql executes cleanly into a fresh SQLite DB."""

from __future__ import annotations

from pathlib import Path

from news_agent.storage.repository import connect, init_db


def test_init_db(tmp_path: Path):
    db = tmp_path / "test.db"
    init_db(db)
    conn = connect(db)
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        expected = {
            "articles",
            "tags",
            "scores",
            "surfaces",
            "corrections",
            "source_priors",
            "tag_suggestions",
            "source_stats",
            "llm_cost",
            "audit_log",
        }
        assert expected.issubset(tables)
    finally:
        conn.close()
