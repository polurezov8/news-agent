"""news-agent CLI entry point. Subcommands stubbed; impls land in later milestones."""

from __future__ import annotations

import typer
from rich.console import Console

from news_agent import __version__
from news_agent.cli.schedule import schedule_app
from news_agent.core.types import Cadence

app = typer.Typer(
    name="news-agent",
    help="Personal news + reading agent. Self-hosted, configurable.",
    no_args_is_help=False,
)

console = Console()
app.add_typer(schedule_app, name="schedule")


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", help="Show version and exit"),
):
    if version:
        console.print(f"news-agent {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        run()


@app.command()
def run(
    cadence: str = typer.Option(
        Cadence.DAILY.value,
        "--cadence", "-c",
        help="daily | priority | weekly",
    ),
    no_slack: bool = typer.Option(False, "--no-slack", help="Skip Slack delivery. LLM calls + DB writes still happen."),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Preview the run without persisting articles/tags/scores or posting to Slack. LLM calls are still real and billed.",
    ),
) -> None:
    """Run the pipeline: ingest → dedup → tag → filter → score → route → notify."""
    from dotenv import load_dotenv

    from news_agent import application

    load_dotenv()

    try:
        cadence_enum = Cadence(cadence)
    except ValueError:
        console.print(f"[red]Invalid cadence: {cadence!r}. Use: daily | priority | weekly[/red]")
        raise typer.Exit(1)
    if cadence_enum is Cadence.ON_DEMAND:
        console.print("[red]on_demand cadence is for `/news` slash command, not `run`.[/red]")
        raise typer.Exit(1)

    def _log(msg: str) -> None:
        console.print(f"[dim]{msg}[/dim]")

    if cadence_enum is Cadence.WEEKLY:
        if dry_run:
            console.print("[yellow]--dry-run not supported for weekly recap; skipping.[/yellow]")
            return
        console.print("[cyan]Building weekly recap…[/cyan]")
        try:
            n = application.run_weekly(no_slack=no_slack, log=_log)
        except application.ApplicationError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
        console.print(f"[green]Done.[/green] items={n}")
        return

    mode_suffix = ", dry-run" if dry_run else (", no_slack" if no_slack else "")
    console.print(f"[cyan]Running pipeline (cadence={cadence_enum.value}{mode_suffix})…[/cyan]")
    try:
        result = application.run_pipeline(
            cadence_enum, dry_run=dry_run, no_slack=no_slack, log=_log,
        )
    except application.ApplicationError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    done_label = "Done (dry-run)" if dry_run else "Done"
    console.print(
        f"[green]{done_label}.[/green] "
        f"fetched={result.fetched} "
        f"new={result.new} "
        f"scored={result.scored} "
        f"digest={len(result.digest_items)} "
        f"priority={len(result.priority_items)} "
        f"posted={result.posted}"
    )
    if dry_run and (result.digest_items or result.priority_items):
        console.print("[dim]Would deliver:[/dim]")
        for article, sr in result.digest_items:
            console.print(f"  [bold]digest[/bold]  {sr.final:.2f}  {article.title}")
        for article, sr in result.priority_items:
            console.print(f"  [bold]priority[/bold]  {sr.final:.2f}  {article.title}")


@app.command()
def status() -> None:
    """Show counters, last-run timestamp, and cost MTD."""
    import os
    from datetime import datetime, timezone
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv()

    from news_agent.llm.costs import cents_to_dollars
    from news_agent.storage.repository import connect, init_db, monthly_usd_cents

    db_path = Path(os.environ.get("NEWS_AGENT_DB", "./news_agent.db"))
    init_db(db_path)
    budget_usd = float(os.environ.get("NEWS_AGENT_BUDGET_USD", "5"))
    budget_cents = int(round(budget_usd * 100))

    conn = connect(db_path)
    try:
        spent_cents = monthly_usd_cents(conn, now=datetime.now(timezone.utc))
        articles = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        scored = conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0]
        surfaces = conn.execute("SELECT COUNT(*) FROM surfaces").fetchone()[0]
        corrections = conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]
        last_run = conn.execute(
            "SELECT MAX(posted_at) FROM surfaces"
        ).fetchone()[0]
    finally:
        conn.close()

    pct = (spent_cents / budget_cents * 100) if budget_cents else 0
    color = "red" if pct >= 95 else ("yellow" if pct >= 75 else "green")
    console.print(
        f"[bold]MTD spend:[/bold] [{color}]{cents_to_dollars(spent_cents)}[/] "
        f"of {cents_to_dollars(budget_cents)} ({pct:.0f}%)"
    )
    console.print(f"[bold]Articles:[/bold] {articles}  scored={scored}  surfaced={surfaces}  corrections={corrections}")
    console.print(f"[bold]Last surface:[/bold] {last_run or '(never)'}")


@app.command()
def doctor() -> None:
    """Run end-to-end health checks."""
    import os
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv()

    from news_agent.config.loader import (
        load_priors, load_sources, load_tags, load_topics,
    )
    from news_agent.storage.repository import connect, init_db

    failures: list[str] = []
    warnings: list[str] = []

    def ok(label: str) -> None:
        console.print(f"  [green]✓[/green] {label}")

    def warn(label: str) -> None:
        console.print(f"  [yellow]⚠[/yellow] {label}")
        warnings.append(label)

    def fail(label: str) -> None:
        console.print(f"  [red]✗[/red] {label}")
        failures.append(label)

    console.print("[bold]Env vars[/bold]")
    for var in ("ANTHROPIC_API_KEY", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_USER_ID"):
        if os.environ.get(var):
            ok(f"{var} set")
        else:
            fail(f"{var} missing")

    console.print("[bold]Configs[/bold]")
    try:
        tags = load_tags()
        topics = load_topics()
        sources = load_sources()
        priors = load_priors()
        ok(f"tags ({sum(len(v) for v in tags.tags.values())} terms)")
        ok(f"topics ({len(topics.topics)})")
        ok(f"sources ({len(sources.sources)} total, {sum(1 for s in sources.sources if s.enabled)} enabled)")
        ok(f"priors ({len(priors.priors)} from yaml)")
        for src in sources.sources:
            if not src.enabled:
                continue
            for t in src.topics:
                if t not in topics.topics:
                    fail(f"source {src.id} references unknown topic {t}")
    except Exception as exc:
        fail(f"config parse: {exc}")
        return

    console.print("[bold]Database[/bold]")
    db_path = Path(os.environ.get("NEWS_AGENT_DB", "./news_agent.db"))
    try:
        init_db(db_path)
        conn = connect(db_path)
        try:
            conn.execute("SELECT 1 FROM articles LIMIT 1")
            ok(f"writable at {db_path}")
        finally:
            conn.close()
    except Exception as exc:
        fail(f"db: {exc}")

    console.print("[bold]Slack[/bold]")
    if os.environ.get("SLACK_BOT_TOKEN"):
        try:
            from slack_sdk import WebClient
            from slack_sdk.errors import SlackApiError

            client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
            auth = client.auth_test()
            ok(f"auth.test OK (team={auth.get('team', '?')}, bot={auth.get('user', '?')})")
        except SlackApiError as exc:
            fail(f"slack auth.test: {exc.response.get('error', '?')}")
        except Exception as exc:
            fail(f"slack: {exc}")
    else:
        warn("skipped (no SLACK_BOT_TOKEN)")

    rsshub_needed = any(
        src.enabled and src.type in ("rsshub", "rsshub_twitter_list")
        for src in sources.sources
    )
    if rsshub_needed:
        console.print("[bold]RSSHub[/bold]")
        try:
            import httpx

            base = os.environ.get("RSSHUB_BASE_URL", "http://localhost:1200")
            r = httpx.get(f"{base.rstrip('/')}/healthz", timeout=3.0)
            if r.status_code == 200:
                ok(f"healthz OK at {base}")
            else:
                fail(f"rsshub healthz HTTP {r.status_code}")
        except Exception as exc:
            fail(f"rsshub unreachable: {exc}")

    console.print()
    if failures:
        console.print(f"[red]{len(failures)} failure(s)[/red]" + (f", {len(warnings)} warning(s)" if warnings else ""))
        raise typer.Exit(1)
    if warnings:
        console.print(f"[yellow]{len(warnings)} warning(s), no failures[/yellow]")
    else:
        console.print("[green]All checks passed.[/green]")


@app.command()
def init() -> None:
    """Interactive setup wizard for tags, topics, sources."""
    console.print("[yellow]init wizard: not yet implemented[/yellow]")


@app.command()
def backup(
    dest: str = typer.Option(
        None, "--dest", help="Destination directory. Default: ~/Backups/news-agent (or $NEWS_AGENT_BACKUP_DIR)."
    ),
    keep: int = typer.Option(7, "--keep", help="Number of recent backups to retain."),
) -> None:
    """Hot-copy news_agent.db to a backup directory and rotate old copies."""
    from pathlib import Path

    from dotenv import load_dotenv

    from news_agent.storage.backup import backup_db

    load_dotenv()
    try:
        out = backup_db(dest_dir=Path(dest).expanduser() if dest else None, keep=keep)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]Backup written:[/green] {out}")


@app.command()
def slack(
    demo: bool = typer.Option(False, "--demo", help="Post a sample digest to verify formatting."),
) -> None:
    """Start Slack Socket Mode listener (commands, buttons, reactions)."""
    import os

    from dotenv import load_dotenv

    load_dotenv()

    missing = [v for v in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_USER_ID") if not os.environ.get(v)]
    if missing:
        console.print(f"[red]Missing env vars: {', '.join(missing)}[/red]")
        raise typer.Exit(1)

    from news_agent.notifier.slack import SlackNotifier
    from news_agent.notifier.listener import make_app, start

    notifier = SlackNotifier.from_env()

    if demo:
        from news_agent.notifier.slack import _demo_payload

        ref = notifier.post_digest(_demo_payload())
        console.print(f"[green]Demo digest posted — ts={ref.message_id}[/green]")
        return

    from pathlib import Path

    from news_agent import application
    from news_agent.core.types import CorrectionEvent
    from news_agent.storage.repository import init_db

    db_path = Path(os.environ.get("NEWS_AGENT_DB", "./news_agent.db"))
    init_db(db_path)

    def _on_correction(ev: CorrectionEvent) -> None:
        application.handle_correction(
            ev,
            db_path=db_path,
            log=lambda msg: console.print(f"[cyan]{msg}[/cyan]"),
        )

    bolt = make_app(notifier=notifier, on_correction=_on_correction, db_path=db_path)
    console.print("[green]Slack Socket Mode listener starting…[/green]")
    start(bolt)


# Sub-typer groups for topic / tag / source / eval / safari / schedule land in M8.


if __name__ == "__main__":
    app()
