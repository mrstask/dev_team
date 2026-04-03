#!/usr/bin/env python3
"""
Autonomous Dev Team

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
from event_loop import run_loop, run_step
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


@cli.command("step")
@click.argument("task_id", type=int)
def step_cmd(task_id: int) -> None:
    """Run the current pipeline step for a task once and exit.

    Fetches TASK_ID, checks its status + action label, runs the matching
    agent handler, and returns. Output is streamed directly to the terminal.

    Use 'board' to see which step each task is waiting on.
    """
    _ensure_backends()
    _sync_agents()
    run_step(task_id)


@cli.command("pending")
def pending_cmd() -> None:
    """List all tasks currently paused at a human gate."""
    from rich.table import Table

    db = DashboardClient(config.DASHBOARD_URL, config.DASHBOARD_PROJECT_ID)
    tasks = db.get_tasks()
    waiting = [t for t in tasks if Action.AWAIT_HUMAN in t.get("labels", [])]

    if not waiting:
        config.console.print("[dim]No tasks waiting for human review.[/dim]")
        return

    tbl = Table(title="Tasks awaiting human review", header_style="bold yellow", show_lines=True)
    tbl.add_column("ID", width=5)
    tbl.add_column("Title", min_width=40)
    tbl.add_column("Status", width=12)
    tbl.add_column("Priority", width=10)
    tbl.add_column("Actions", min_width=50)
    for t in waiting:
        tid = t["id"]
        tbl.add_row(
            str(tid),
            t.get("title", ""),
            t.get("status", ""),
            t.get("priority", ""),
            f"review {tid}  |  approve {tid}  |  reject {tid} \"feedback\"",
        )
    config.console.print(tbl)


@cli.command("review")
@click.argument("task_id", type=int)
def review_cmd(task_id: int) -> None:
    """Show agent output and spec contract for a human-gated task."""
    import json
    from core.spec_loader import spec_summary_for_stage

    db = DashboardClient(config.DASHBOARD_URL, config.DASHBOARD_PROJECT_ID)
    task = db.get_task(task_id)

    if Action.AWAIT_HUMAN not in task.get("labels", []):
        config.console.print(f"[yellow]Task #{task_id} is not waiting for human review.[/yellow]")
        return

    status = task.get("status", "")
    context_dir = config.CONTEXT_DIR / str(task_id)

    config.console.rule(f"[bold]Review — #{task_id} {task.get('title', '')}[/bold]", style="yellow")
    config.console.print(f"[dim]Status:[/dim] {status}   [dim]Priority:[/dim] {task.get('priority', '')}")
    config.console.print()

    # Detect which stage just completed and show its output
    stage, ctx = None, None
    for candidate_stage, ctx_key in [("testing", "testing"), ("develop", "developer"), ("architect", "architect")]:
        path = context_dir / f"{ctx_key}.json"
        if path.exists():
            stage = candidate_stage
            ctx = json.loads(path.read_text(encoding="utf-8"))
            break

    if stage and ctx:
        files = ctx.get("files", [])
        summary = ctx.get("summary", "") or ctx.get("ci_result", {}).get("status", "")
        config.console.print(f"[bold green]Stage completed:[/bold green] {stage}")
        config.console.print(f"[bold]Summary:[/bold] {summary[:300] if summary else '(none)'}")
        config.console.print(f"[bold]Files ({len(files)}):[/bold]")
        for f in files[:20]:
            path_str = f.get("path") if isinstance(f, dict) else getattr(f, "path", str(f))
            config.console.print(f"  [cyan]{path_str}[/cyan]")
        if len(files) > 20:
            config.console.print(f"  [dim]… {len(files) - 20} more[/dim]")

        # Show CI result for testing stage
        if stage == "testing" and "ci_result" in ctx:
            ci = ctx["ci_result"]
            color = "green" if ci.get("status") == "committed" else "red"
            config.console.print(f"\n[bold]CI status:[/bold] [{color}]{ci.get('status')}[/{color}]")
            if ci.get("output"):
                config.console.print(f"[dim]{ci['output'][-800:]}[/dim]")
    else:
        config.console.print("[dim]No agent output found in context yet.[/dim]")

    # Show relevant spec section
    config.console.print()
    config.console.rule("[bold]Spec contract for this stage[/bold]", style="dim")
    config.console.print(spec_summary_for_stage(stage or status))
    config.console.print()
    config.console.print(
        f"[bold yellow]Decision:[/bold yellow]\n"
        f"  [green]python main.py approve {task_id}[/green]           continue to next stage\n"
        f"  [red]python main.py reject {task_id} \"notes\"[/red]   retry with your feedback"
    )


@cli.command("approve")
@click.argument("task_id", type=int)
def approve_cmd(task_id: int) -> None:
    """Approve a human-gated task — releases it to the next pipeline stage."""
    db = DashboardClient(config.DASHBOARD_URL, config.DASHBOARD_PROJECT_ID)
    task = db.get_task(task_id)

    if Action.AWAIT_HUMAN not in task.get("labels", []):
        config.console.print(f"[yellow]Task #{task_id} is not waiting for human review.[/yellow]")
        return

    labels = [l for l in task.get("labels", []) if not l.startswith(Action.PREFIX)]
    labels.append(Action.REVIEW)
    db.set_labels(task_id, labels)
    db.log_event(task_id, "human:approved", {"approved_by": "human", "task_id": task_id})
    config.console.print(f"[bold green]✓ Task #{task_id} approved — continuing to next pipeline stage.[/bold green]")


@cli.command("reject")
@click.argument("task_id", type=int)
@click.argument("feedback")
def reject_cmd(task_id: int, feedback: str) -> None:
    """Reject a human-gated task with feedback — resets it to action:todo for retry."""
    import re

    db = DashboardClient(config.DASHBOARD_URL, config.DASHBOARD_PROJECT_ID)
    task = db.get_task(task_id)

    if Action.AWAIT_HUMAN not in task.get("labels", []):
        config.console.print(f"[yellow]Task #{task_id} is not waiting for human review.[/yellow]")
        return

    # Increment retry counter
    current_retry = 0
    for label in task.get("labels", []):
        m = re.match(r"^retry:(\d+)$", label)
        if m:
            current_retry = int(m.group(1))
            break

    labels = [
        l for l in task.get("labels", [])
        if not l.startswith(Action.PREFIX) and not l.startswith("retry:")
    ]
    labels.append(Action.TODO)
    labels.append(f"retry:{current_retry + 1}")
    db.set_labels(task_id, labels)

    # Append feedback to description
    fresh = db.get_task(task_id)
    db.append_review_feedback(task_id, fresh, {
        "issues": [feedback],
        "overall_comment": "Human reviewer rejected",
    })
    db.log_event(task_id, "human:rejected", {
        "feedback": feedback,
        "retry": current_retry + 1,
    })
    config.console.print(
        f"[bold red]✗ Task #{task_id} rejected — feedback appended, reset to action:todo "
        f"(retry {current_retry + 1}/{config.MAX_TASK_RETRIES}).[/bold red]"
    )


@cli.command("suggestions")
@click.option("--status", default="open", show_default=True, help="Filter by status: open | applied | dismissed | all")
def suggestions_cmd(status: str) -> None:
    """Print open prompt improvement suggestions grouped by agent role."""
    from rich.table import Table

    db = DashboardClient(config.DASHBOARD_URL, config.DASHBOARD_PROJECT_ID)
    items = db.get_suggestions(status=None if status == "all" else status)

    if not items:
        config.console.print(f"[dim]No suggestions with status '{status}'.[/dim]")
        return

    by_role: dict[str, list] = {}
    for s in items:
        by_role.setdefault(s["agent_role"], []).append(s)

    _STATUS_COLOR = {"open": "yellow", "applied": "green", "dismissed": "dim"}

    for role, suggestions in sorted(by_role.items()):
        tbl = Table(title=f"[bold]{role}[/bold] ({len(suggestions)})", header_style="bold", show_lines=True)
        tbl.add_column("ID", width=4)
        tbl.add_column("Issue Pattern", min_width=32)
        tbl.add_column("Suggested Instruction", min_width=42)
        tbl.add_column("Status", width=10)
        tbl.add_column("Task", width=6)
        for s in suggestions:
            color = _STATUS_COLOR.get(s["status"], "white")
            tbl.add_row(
                str(s["id"]),
                s["issue_pattern"][:80],
                s["suggested_instruction"][:100],
                f"[{color}]{s['status']}[/{color}]",
                str(s["task_id"]),
            )
        config.console.print(tbl)


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
        config.console.print(f"Dashboard ({config.DASHBOARD_URL})  [green]OK[/green]  ({total} tasks)")
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
        config.console.print("[red]OPENROUTER_API_KEY not set. Add it to .env[/red]")
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
