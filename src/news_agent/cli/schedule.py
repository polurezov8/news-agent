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

LABEL_PREFIX = "com.polurezov.news-agent"
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
    bin_path: str,
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
        f"        <string>{bin_path}</string>\n"
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


def _plist_path(cadence: Cadence) -> Path:
    return LAUNCH_AGENTS_DIR / f"{LABEL_PREFIX}.{cadence.value}.plist"


def _resolve_bin() -> str:
    import sys

    bin_path = shutil.which("news-agent")
    if bin_path:
        return bin_path
    candidate = Path(sys.prefix) / "bin" / "news-agent"
    if candidate.exists():
        return str(candidate)
    console.print("[red]`news-agent` not found. Activate the venv or `uv sync`.[/red]")
    raise typer.Exit(1)


@schedule_app.command()
def install() -> None:
    """Write plists to ~/Library/LaunchAgents and launchctl load them."""
    bin_path = _resolve_bin()
    working_dir = str(Path.cwd())
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    for cadence in _SCHEDULED_CADENCES:
        path = _plist_path(cadence)
        path.write_text(generate_plist(
            cadence,
            bin_path=bin_path,
            working_dir=working_dir,
            log_dir=str(LOG_DIR),
        ))
        # Unload first in case already loaded (idempotent install).
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        result = subprocess.run(
            ["launchctl", "load", str(path)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            console.print(f"[red]load failed[/red] {cadence.value}: {result.stderr.strip()}")
        else:
            console.print(f"[green]installed[/green] {cadence.value}  →  {path}")


@schedule_app.command()
def uninstall() -> None:
    """Unload and delete plists."""
    for cadence in _SCHEDULED_CADENCES:
        path = _plist_path(cadence)
        if path.exists():
            subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
            path.unlink()
            console.print(f"[yellow]removed[/yellow] {cadence.value}")
        else:
            console.print(f"[dim]not installed[/dim] {cadence.value}")


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
        path = _plist_path(cadence)
        if not path.exists():
            console.print(f"[dim]not installed[/dim] {cadence.value}")
            continue
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        result = subprocess.run(
            ["launchctl", "load", str(path)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            console.print(f"[green]restarted[/green] {cadence.value}")
        else:
            console.print(f"[red]restart failed[/red] {cadence.value}: {result.stderr.strip()}")
