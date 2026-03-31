#!/usr/bin/env python3
"""
Habr Agentic Pipeline — Dev Team CLI

You are the Tech/Project Manager. This tool manages a team of local LLM
coding agents (powered by Ollama) that implement the habr-agentic project.

Usage:
  python main.py            # start interactive PM session (default)
  python main.py session    # same as above
  python main.py board      # print task board
  python main.py run <id>   # run specific task by ID
  python main.py status     # check Ollama + dashboard health
"""
import sys

import click
from rich.console import Console

import config
from ollama_client import OllamaClient
from dashboard_client import DashboardClient
from orchestrator import session, show_board, run_task
from roles import ROLES

console = Console()


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Dev team CLI — you are the Tech/Project Manager."""
    if ctx.invoked_subcommand is None:
        _ensure_ollama()
        _sync_agents()
        session()


@cli.command("session")
def session_cmd() -> None:
    """Start interactive PM session (default)."""
    _ensure_ollama()
    _sync_agents()
    session()


@cli.command("board")
def board_cmd() -> None:
    """Print current task board."""
    show_board()


@cli.command("run")
@click.argument("task_id", type=int)
def run_cmd(task_id: int) -> None:
    """Run a specific task by ID."""
    _ensure_ollama()
    d    = DashboardClient(config.DASHBOARD_URL, config.DASHBOARD_PROJECT_ID)
    task = d.get_task(task_id)
    run_task(task)


@cli.command("status")
def status_cmd() -> None:
    """Check Ollama, OpenRouter, and dashboard API health."""
    # ── Per-step model table ───────────────────────────────────────────────────
    from rich.table import Table
    _BACKEND_COLOR = {"claude-code": "magenta", "openrouter": "cyan", "ollama": "yellow"}
    tbl = Table(title="Step configuration", header_style="bold")
    tbl.add_column("Step",    width=12)
    tbl.add_column("Backend", width=14)
    tbl.add_column("Model",   min_width=30)
    tbl.add_column("Status",  width=16)
    for name, s in config.STEPS.items():
        color  = _BACKEND_COLOR.get(s["backend"], "white")
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
    console.print(tbl)

    # ── Dashboard ─────────────────────────────────────────────────────────────
    console.print()
    try:
        d     = DashboardClient(config.DASHBOARD_URL, config.DASHBOARD_PROJECT_ID)
        tasks = d.get_tasks()
        by_s: dict[str, int] = {}
        for t in tasks:
            by_s[t["status"]] = by_s.get(t["status"], 0) + 1
        total = sum(by_s.values())
        console.print(f"Dashboard ({config.DASHBOARD_URL})  [green]OK[/green]  ({total} tasks in HAP)")
        for s, n in sorted(by_s.items()):
            console.print(f"  {s}: {n}")
    except Exception as e:
        console.print(f"Dashboard: [red]ERROR — {e}[/red]")


# ── Guards ────────────────────────────────────────────────────────────────────

def _ensure_ollama() -> None:
    ollama_steps = {name: s for name, s in config.STEPS.items() if s["backend"] == "ollama"}
    if not ollama_steps:
        return  # no ollama steps configured

    client = OllamaClient(config.OLLAMA_URL, next(iter(ollama_steps.values()))["model"])
    if not client.is_alive():
        console.print("[red]Ollama is offline.  Start it:[/red]  ollama serve")
        sys.exit(1)

    missing = [s["model"] for s in ollama_steps.values() if not client.is_model_available(s["model"])]
    if missing:
        pulls = "\n".join(f"  ollama pull {m}" for m in missing)
        console.print(f"[yellow]Models not pulled — run:[/yellow]\n{pulls}")
        sys.exit(1)


def _sync_agents() -> None:
    try:
        d = DashboardClient(config.DASHBOARD_URL, config.DASHBOARD_PROJECT_ID)
        d.sync_agents(ROLES)
        console.print("[dim]Synced agents to dashboard.[/dim]")
    except Exception as e:
        console.print(f"[yellow]Failed to sync agents to dashboard: {e}[/yellow]")


if __name__ == "__main__":
    cli()
