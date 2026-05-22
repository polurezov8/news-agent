"""SQLite hot backup with rotation."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path


DEFAULT_BACKUP_DIR = Path.home() / "Backups" / "news-agent"
DEFAULT_KEEP = 7
BACKUP_SUFFIX = ".db"


def _source_path() -> Path:
    return Path(os.environ.get("NEWS_AGENT_DB", "./news_agent.db"))


def _default_backup_dir() -> Path:
    override = os.environ.get("NEWS_AGENT_BACKUP_DIR")
    if override:
        return Path(override).expanduser()
    return DEFAULT_BACKUP_DIR


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _backup_filename(src: Path) -> str:
    return f"{src.stem}-{_timestamp()}{BACKUP_SUFFIX}"


def _existing_backups(dest_dir: Path, src: Path) -> list[Path]:
    pattern = f"{src.stem}-*{BACKUP_SUFFIX}"
    return sorted(dest_dir.glob(pattern))


def _rotate(dest_dir: Path, src: Path, keep: int) -> list[Path]:
    files = _existing_backups(dest_dir, src)
    if len(files) <= keep:
        return []
    to_delete = files[: len(files) - keep]
    for p in to_delete:
        p.unlink(missing_ok=True)
    return to_delete


def backup_db(
    *,
    dest_dir: Path | None = None,
    keep: int = DEFAULT_KEEP,
) -> Path:
    """Copy the live SQLite database to dest_dir using a hot backup.

    Uses sqlite3.Connection.backup() so concurrent writers stay safe.
    Returns the path of the new backup file. Older backups beyond `keep`
    are deleted (oldest first).
    """
    src = _source_path()
    if not src.exists():
        raise FileNotFoundError(f"database not found: {src}")

    target_dir = dest_dir or _default_backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    out = target_dir / _backup_filename(src)
    src_conn = sqlite3.connect(str(src))
    try:
        dest_conn = sqlite3.connect(str(out))
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()

    _rotate(target_dir, src, keep)
    return out
