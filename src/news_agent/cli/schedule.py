"""launchd schedule management. macOS only for v1.0."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import typer
from rich.console import Console

from news_agent.core.types import Cadence

schedule_app = typer.Typer(help="Manage launchd schedules (macOS).")
console = Console()

LABEL_PREFIX = "news-agent"
LISTENER_LABEL = f"{LABEL_PREFIX}.listener"
BACKUP_LABEL = f"{LABEL_PREFIX}.backup"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LOG_DIR = Path.home() / "Library" / "Logs" / "news-agent"

_SCHEDULED_CADENCES: tuple[Cadence, ...] = (Cadence.DAILY, Cadence.PRIORITY, Cadence.WEEKLY)


def _schedule_block(cadence: Cadence) -> str:
    if cadence is Cadence.DAILY:
        return (
            "    <key>StartCalendarInterval</key>\n"
            "    <dict>\n"
            "        <key>Hour</key><integer>10</integer>\n"
            "        <key>Minute</key><integer>0</integer>\n"
            "    </dict>"
        )
    if cadence is Cadence.PRIORITY:
        return "    <key>StartInterval</key>\n    <integer>3600</integer>"
    if cadence is Cadence.WEEKLY:
        return (
            "    <key>StartCalendarInterval</key>\n"
            "    <dict>\n"
            "        <key>Weekday</key><integer>0</integer>\n"
            "        <key>Hour</key><integer>10</integer>\n"
            "        <key>Minute</key><integer>0</integer>\n"
            "    </dict>"
        )
    raise ValueError(f"unschedulable cadence: {cadence}")


def generate_plist(
    cadence: Cadence,
    *,
    uv_path: str,
    working_dir: str,
    log_dir: str,
) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        f"    <key>Label</key>\n    <string>{LABEL_PREFIX}.{cadence.value}</string>\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        f"        <string>{uv_path}</string>\n"
        "        <string>run</string>\n"
        "        <string>news-agent</string>\n"
        "        <string>run</string>\n"
        "        <string>--cadence</string>\n"
        f"        <string>{cadence.value}</string>\n"
        "    </array>\n"
        f"    <key>WorkingDirectory</key>\n    <string>{working_dir}</string>\n"
        f"{_schedule_block(cadence)}\n"
        f"    <key>StandardOutPath</key>\n    <string>{log_dir}/{cadence.value}.log</string>\n"
        f"    <key>StandardErrorPath</key>\n    <string>{log_dir}/{cadence.value}.err</string>\n"
        "    <key>RunAtLoad</key><false/>\n"
        "    <key>EnvironmentVariables</key>\n"
        "    <dict>\n"
        "        <key>PATH</key>\n"
        "        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>\n"
        "    </dict>\n"
        "</dict>\n"
        "</plist>\n"
    )


def generate_listener_plist(
    *,
    uv_path: str,
    working_dir: str,
    log_dir: str,
) -> str:
    """Plist for the reaction + chat listener daemon — RunAtLoad + KeepAlive, no schedule."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        f"    <key>Label</key>\n    <string>{LISTENER_LABEL}</string>\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        f"        <string>{uv_path}</string>\n"
        "        <string>run</string>\n"
        "        <string>news-agent</string>\n"
        "        <string>slack</string>\n"
        "    </array>\n"
        f"    <key>WorkingDirectory</key>\n    <string>{working_dir}</string>\n"
        "    <key>RunAtLoad</key>\n    <true/>\n"
        "    <key>KeepAlive</key>\n    <true/>\n"
        f"    <key>StandardOutPath</key>\n    <string>{log_dir}/listener.log</string>\n"
        f"    <key>StandardErrorPath</key>\n    <string>{log_dir}/listener.err</string>\n"
        "    <key>EnvironmentVariables</key>\n"
        "    <dict>\n"
        "        <key>PATH</key>\n"
        "        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>\n"
        "    </dict>\n"
        "</dict>\n"
        "</plist>\n"
    )


def generate_backup_plist(
    *,
    uv_path: str,
    working_dir: str,
    log_dir: str,
) -> str:
    """Daily 09:00 backup of news_agent.db."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        f"    <key>Label</key>\n    <string>{BACKUP_LABEL}</string>\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        f"        <string>{uv_path}</string>\n"
        "        <string>run</string>\n"
        "        <string>news-agent</string>\n"
        "        <string>backup</string>\n"
        "    </array>\n"
        f"    <key>WorkingDirectory</key>\n    <string>{working_dir}</string>\n"
        "    <key>StartCalendarInterval</key>\n"
        "    <dict>\n"
        "        <key>Hour</key><integer>9</integer>\n"
        "        <key>Minute</key><integer>0</integer>\n"
        "    </dict>\n"
        f"    <key>StandardOutPath</key>\n    <string>{log_dir}/backup.log</string>\n"
        f"    <key>StandardErrorPath</key>\n    <string>{log_dir}/backup.err</string>\n"
        "    <key>RunAtLoad</key><false/>\n"
        "    <key>EnvironmentVariables</key>\n"
        "    <dict>\n"
        "        <key>PATH</key>\n"
        "        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>\n"
        "    </dict>\n"
        "</dict>\n"
        "</plist>\n"
    )


def _plist_path(cadence: Cadence) -> Path:
    return LAUNCH_AGENTS_DIR / f"{LABEL_PREFIX}.{cadence.value}.plist"


def _listener_plist_path() -> Path:
    return LAUNCH_AGENTS_DIR / f"{LISTENER_LABEL}.plist"


def _backup_plist_path() -> Path:
    return LAUNCH_AGENTS_DIR / f"{BACKUP_LABEL}.plist"


def _resolve_uv() -> str:
    """Path to the `uv` binary. launchd must exec `uv run news-agent …`, never the
    project's `.venv/bin/news-agent` directly: on this Mac the venv lives under
    ~/Desktop and macOS TCC blocks launchd from exec'ing binaries there
    (Operation not permitted on pyvenv.cfg). uv lives outside Desktop, and
    `uv run` resolves the project venv from WorkingDirectory."""
    for cand in ("/opt/homebrew/bin/uv", shutil.which("uv")):
        if cand and Path(cand).exists():
            return cand
    console.print("[red]`uv` not found at /opt/homebrew/bin/uv or on PATH.[/red]")
    raise typer.Exit(1)


def _load_plist(path: Path, label: str) -> None:
    """Idempotent load: unload first, then load. Echo result."""
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
    result = subprocess.run(
        ["launchctl", "load", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        console.print(f"[red]load failed[/red] {label}: {result.stderr.strip()}")
    else:
        console.print(f"[green]installed[/green] {label}  →  {path}")


@schedule_app.command()
def install() -> None:
    """Write plists to ~/Library/LaunchAgents and launchctl load them."""
    uv_path = _resolve_uv()
    working_dir = str(Path.cwd())
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    for cadence in _SCHEDULED_CADENCES:
        path = _plist_path(cadence)
        path.write_text(generate_plist(
            cadence,
            uv_path=uv_path,
            working_dir=working_dir,
            log_dir=str(LOG_DIR),
        ))
        _load_plist(path, cadence.value)

    listener_path = _listener_plist_path()
    listener_path.write_text(generate_listener_plist(
        uv_path=uv_path,
        working_dir=working_dir,
        log_dir=str(LOG_DIR),
    ))
    _load_plist(listener_path, "listener")

    backup_path = _backup_plist_path()
    backup_path.write_text(generate_backup_plist(
        uv_path=uv_path,
        working_dir=working_dir,
        log_dir=str(LOG_DIR),
    ))
    _load_plist(backup_path, "backup")


@schedule_app.command()
def uninstall() -> None:
    """Unload and delete plists."""
    for cadence in _SCHEDULED_CADENCES:
        _remove_plist(_plist_path(cadence), cadence.value)
    _remove_plist(_listener_plist_path(), "listener")
    _remove_plist(_backup_plist_path(), "backup")


def _remove_plist(path: Path, label: str) -> None:
    if path.exists():
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        path.unlink()
        console.print(f"[yellow]removed[/yellow] {label}")
    else:
        console.print(f"[dim]not installed[/dim] {label}")


@schedule_app.command()
def status() -> None:
    """Show launchctl status for installed jobs."""
    result = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
    found = False
    for line in result.stdout.splitlines():
        if LABEL_PREFIX in line:
            console.print(line)
            found = True
    if not found:
        console.print("[yellow]no news-agent jobs loaded[/yellow]")


@schedule_app.command()
def restart() -> None:
    """Unload + load every installed job."""
    for cadence in _SCHEDULED_CADENCES:
        _restart_plist(_plist_path(cadence), cadence.value)
    _restart_plist(_listener_plist_path(), "listener")
    _restart_plist(_backup_plist_path(), "backup")


def _restart_plist(path: Path, label: str) -> None:
    if not path.exists():
        console.print(f"[dim]not installed[/dim] {label}")
        return
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
    result = subprocess.run(
        ["launchctl", "load", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        console.print(f"[green]restarted[/green] {label}")
    else:
        console.print(f"[red]restart failed[/red] {label}: {result.stderr.strip()}")
