#!/usr/bin/env python3
"""
Habr Agentic Pipeline — Autonomous Dev Team

Runs a fully autonomous event loop that polls the task dashboard and
dispatches work to agents based on task status + action labels.

Usage:
  python main.py              # start the autonomous event loop (default)
  python main.py run          # same as above
  python main.py board        # print task board
  python main.py kick <id>    # move a backlog task into architect + action:todo
  python main.py status       # check health of Ollama, OpenRouter, dashboard
"""
import sys

import click

import config
from clients import DashboardClient, OllamaClient
from core import ROLES
from dtypes import Action, Status
from event_loop import run_loop
from orchestrator import show_board


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Autonomous dev team — runs event loop by default."""
    if ctx.invoked_subcommand is None:
        _ensure_backends()
        _sync_agents()
        run_loop()


@cli.command("run")
@click.option("--poll-interval", default=config.EVENT_LOOP_POLL_INTERVAL, help="Seconds between polls")
def run_cmd(poll_interval: int) -> None:
    """Start the autonomous event loop."""
    _ensure_backends()
    _sync_agents()
    run_loop(poll_interval=poll_interval)


@cli.command("board")
@click.option("--status", default=None, help="Filter by status")
def board_cmd(status: str | None) -> None:
    """Display current task board."""
    show_board(status_filter=status)


@cli.command("kick")
@click.argument("task_id", type=int)
def kick_cmd(task_id: int) -> None:
    """Move a backlog task into architect + action:todo to start processing."""
    db = DashboardClient(config.DASHBOARD_URL, config.DASHBOARD_PROJECT_ID)
    task = db.get_task(task_id)

    if task["status"] != Status.BACKLOG:
        config.console.print(f"[yellow]Task #{task_id} is in '{task['status']}', not backlog. Skipping.[/yellow]")
        return

    db.move_task(task_id, Status.ARCHITECT)
    labels = list(task.get("labels", []))
    if Action.TODO not in labels:
        labels.append(Action.TODO)
    db.set_labels(task_id, labels)
    config.console.print(f"[green]Task #{task_id} moved to architect + action:todo.[/green]")


@cli.command("status")
def status_cmd() -> None:
    """Check Ollama, OpenRouter, and dashboard API health."""
    from rich.table import Table

    _BACKEND_COLOR = {"claude-code": "magenta", "openrouter": "cyan", "ollama": "yellow"}
    tbl = Table(title="Step configuration", header_style="bold")
    tbl.add_column("Step", width=12)
    tbl.add_column("Backend", width=14)
    tbl.add_column("Model", min_width=30)
    tbl.add_column("Status", width=16)
    for name, s in config.STEPS.items():
        color = _BACKEND_COLOR.get(s["backend"], "white")
        status = "[dim]n/a[/dim]"
        if s["backend"] == "ollama":
            client = OllamaClient(config.OLLAMA_URL, s["model"])
            if client.is_alive():
                status = "[green]✓ pulled[/green]" if client.is_model_available(s["model"]) else "[yellow]not pulled[/yellow]"
            else:
                status = "[red]ollama offline[/red]"
        elif s["backend"] == "openrouter":
            status = "[green]key set[/green]" if config.OPENROUTER_API_KEY else "[red]no API key[/red]"
        elif s["backend"] == "claude-code":
            status = "[green]local CLI[/green]"
        tbl.add_row(name, f"[{color}]{s['backend']}[/{color}]", s["model"], status)
    config.console.print(tbl)

    config.console.print()
    try:
        d = DashboardClient(config.DASHBOARD_URL, config.DASHBOARD_PROJECT_ID)
        tasks = d.get_tasks()
        by_s: dict[str, int] = {}
        for t in tasks:
            by_s[t["status"]] = by_s.get(t["status"], 0) + 1
        total = sum(by_s.values())
        config.console.print(f"Dashboard ({config.DASHBOARD_URL})  [green]OK[/green]  ({total} tasks in HAP)")
        for s, n in sorted(by_s.items()):
            config.console.print(f"  {s}: {n}")
    except Exception as e:
        config.console.print(f"Dashboard: [red]ERROR — {e}[/red]")


# ── Guards ────────────────────────────────────────────────────────────────────

def _ensure_backends() -> None:
    """Check that required backends are available."""
    ollama_steps = {name: s for name, s in config.STEPS.items() if s["backend"] == "ollama"}
    if ollama_steps:
        client = OllamaClient(config.OLLAMA_URL, next(iter(ollama_steps.values()))["model"])
        if not client.is_alive():
            config.console.print("[red]Ollama is offline.  Start it:[/red]  ollama serve")
            sys.exit(1)
        missing = [s["model"] for s in ollama_steps.values() if not client.is_model_available(s["model"])]
        if missing:
            pulls = "\n".join(f"  ollama pull {m}" for m in missing)
            config.console.print(f"[yellow]Models not pulled — run:[/yellow]\n{pulls}")
            sys.exit(1)

    openrouter_steps = {name: s for name, s in config.STEPS.items() if s["backend"] == "openrouter"}
    if openrouter_steps and not config.OPENROUTER_API_KEY:
        config.console.print("[red]OPENROUTER_API_KEY not set. Add it to habr-agentic/.env[/red]")
        sys.exit(1)


def _sync_agents() -> None:
    """Register missing agent roles in the dashboard."""
    try:
        d = DashboardClient(config.DASHBOARD_URL, config.DASHBOARD_PROJECT_ID)
        d.sync_agents(ROLES)
        config.console.print("[dim]Synced agents to dashboard.[/dim]")
    except Exception as e:
        config.console.print(f"[yellow]Failed to sync agents to dashboard: {e}[/yellow]")


if __name__ == "__main__":
    cli()
