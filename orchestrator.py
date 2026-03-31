"""PM orchestration session — pick tasks, run agents, review output, write files."""
import json
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table

import config
from agent import DevAgent
from ci_agent import CIAgent
from claude_agent import ClaudeAgent
from dashboard_client import DashboardClient
from reviewer import ReviewerAgent
from roles import get_role_for_task
from tester import TestAgent

console = Console()

_db = DashboardClient(config.DASHBOARD_URL, config.DASHBOARD_PROJECT_ID)

_PRIORITY_COLOR = {"critical": "red", "high": "yellow", "medium": "blue", "low": "dim"}
_STATUS_ORDER   = ["backlog", "ready", "running", "review", "done", "failed"]


# ── Board display ─────────────────────────────────────────────────────────────

def show_board(status_filter: str | None = None) -> None:
    tasks = _db.get_tasks(status=status_filter)
    by_status: dict[str, list] = {}
    for t in tasks:
        by_status.setdefault(t["status"], []).append(t)

    for status in _STATUS_ORDER:
        items = by_status.get(status, [])
        if not items:
            continue
        tbl = Table(
            title=f"[bold]{status.upper()}[/bold] ({len(items)})",
            header_style="bold",
            show_lines=False,
        )
        tbl.add_column("ID",    width=4)
        tbl.add_column("Title", min_width=42)
        tbl.add_column("Pri",   width=9)
        tbl.add_column("Phase", width=8)
        tbl.add_column("Agent", width=20)
        for t in items:
            phase = next((l for l in t.get("labels", []) if l.startswith("phase-")), "-")
            agent = next((l for l in t.get("labels", []) if not l.startswith("phase-")), "-")
            pc    = _PRIORITY_COLOR.get(t["priority"], "white")
            tbl.add_row(
                str(t["id"]),
                t["title"][:55],
                f"[{pc}]{t['priority']}[/{pc}]",
                phase,
                agent,
            )
        console.print(tbl)


# ── Task picker ───────────────────────────────────────────────────────────────

def pick_task() -> dict | None:
    tasks = _db.get_tasks(status="backlog")
    if not tasks:
        console.print("[yellow]No backlog tasks.[/yellow]")
        return None

    # Sort: critical first, then high, medium, low
    tasks.sort(key=lambda t: {"critical": 0, "high": 1, "medium": 2, "low": 3}[t["priority"]])

    tbl = Table(title="[bold cyan]Backlog[/bold cyan]", header_style="bold cyan")
    tbl.add_column("#",     width=4)
    tbl.add_column("ID",    width=4)
    tbl.add_column("Title", min_width=42)
    tbl.add_column("Pri",   width=9)
    tbl.add_column("Phase", width=8)
    tbl.add_column("Agent", width=20)
    for i, t in enumerate(tasks, 1):
        phase = next((l for l in t.get("labels", []) if l.startswith("phase-")), "-")
        agent = next((l for l in t.get("labels", []) if not l.startswith("phase-")), "-")
        pc    = _PRIORITY_COLOR.get(t["priority"], "white")
        tbl.add_row(
            str(i), str(t["id"]), t["title"][:52],
            f"[{pc}]{t['priority']}[/{pc}]", phase, agent,
        )
    console.print(tbl)

    choice = Prompt.ask("\nTask # or ID  (q to cancel)").strip()
    if choice.lower() in ("q", ""):
        return None
    try:
        idx = int(choice)
        if 1 <= idx <= len(tasks):
            return tasks[idx - 1]
        for t in tasks:          # try by raw ID
            if t["id"] == idx:
                return t
    except ValueError:
        pass
    console.print("[red]Invalid selection.[/red]")
    return None


# ── Task flow ─────────────────────────────────────────────────────────────────

def run_task(task: dict, feedback: str = "") -> bool:
    """
    Full pipeline:
      1. PM confirms
      2. task → running
      3. Dev agent runs (ReAct loop)
      4. task → review
      5. Reviewer agent checks output
         REJECTED → append issues to task description, task → ready
         APPROVED → PM previews files, confirms
                    approve → write files, task → done
                    revise  → PM note, task → ready, re-run on next pick
    Returns True if task reached done.
    """
    role = get_role_for_task(task)
    if not role:
        console.print(f"[red]No agent role for labels: {task.get('labels', [])}[/red]")
        return False

    is_retry = "REVIEW FEEDBACK:" in task.get("description", "")

    # For architect tasks on retry, show the effective agent
    effective_role = "developer (retry)" if role == "architect" and is_retry else role
    _print_task_header(task, effective_role)

    if not Confirm.ask("Run this task?", default=True):
        return False

    # ── 1. Running ────────────────────────────────────────────────────────────
    _db.move_task(task["id"], "running")
    console.print(f"[dim]  status → running[/dim]")

    if role == "architect" and not is_retry:
        # First attempt: Claude architect produces skeletons, Ollama developer implements them
        arch_result = ClaudeAgent("architect").run(task, feedback)
        if not arch_result:
            console.print("[red]Architect produced no output.[/red]")
            action = Prompt.ask("Action", choices=["ready", "failed"], default="ready")
            _db.move_task(task["id"], action)
            return False
        skeleton_files = arch_result.get("files", [])
        console.print(
            f"\n[bold cyan]Architect done — {len(skeleton_files)} skeleton(s). "
            f"Handing off to Developer...[/bold cyan]"
        )
        result = DevAgent("developer").run(task, feedback, skeleton_files=skeleton_files)
    elif role == "architect" and is_retry:
        # Retry: skeletons already done — developer fixes reviewer issues directly
        console.print("[dim]  Retry detected — skipping Architect, running Developer with feedback.[/dim]")
        previous_files = _load_retry_context(task["id"])
        result = DevAgent("developer").run(task, feedback, previous_files=previous_files)
    else:
        previous_files = _load_retry_context(task["id"])
        result = DevAgent(role).run(task, feedback, previous_files=previous_files)

    if not result:
        console.print("[red]Agent produced no output.[/red]")
        action = Prompt.ask("Action", choices=["ready", "failed"], default="ready")
        _db.move_task(task["id"], action)
        return False

    files   = result.get("files", [])
    summary = result.get("summary", "")

    console.print(Panel(f"[bold]Agent summary:[/bold]\n{summary}", border_style="cyan"))
    console.print(f"[bold]{len(files)} file(s):[/bold]")
    for i, f in enumerate(files, 1):
        console.print(f"  [{i}] [cyan]{f['path']}[/cyan]  ({len(f['content'])} chars)")

    # ── 2. Review ─────────────────────────────────────────────────────────────
    _db.move_task(task["id"], "review")
    console.print(f"[dim]  status → review[/dim]")

    review = ReviewerAgent().review(task, files, summary)

    if not review["approved"]:
        # Reviewer rejected — persist files for next retry, write issues to task
        _save_retry_context(task["id"], files)
        fresh = _db.get_task(task["id"])
        _db.append_review_feedback(task["id"], fresh, review)
        _db.move_task(task["id"], "ready")
        console.print(
            f"[yellow]  status → ready  "
            f"(reviewer found {len(review.get('issues', []))} issue(s) — see task description)[/yellow]"
        )
        return False

    # ── 3. Test generation ────────────────────────────────────────────────────
    test_files = TestAgent().generate_tests(task, files) or []
    all_files  = files + test_files

    # ── 4. PM final approval ──────────────────────────────────────────────────
    console.print(Rule())
    console.print(f"[bold]{len(all_files)} file(s) total ({len(files)} impl + {len(test_files)} test):[/bold]")
    impl_count = len(files)
    for i, f in enumerate(all_files, 1):
        tag = "[dim](test)[/dim]" if i > impl_count else ""
        console.print(f"  [{i}] [cyan]{f['path']}[/cyan]  ({len(f['content'])} chars) {tag}")

    preview = Prompt.ask("Preview file # (Enter to skip)", default="").strip()
    if preview.isdigit():
        idx = int(preview) - 1
        if 0 <= idx < len(all_files):
            _preview_file(all_files[idx])

    decision = Prompt.ask(
        "Reviewer approved — your decision",
        choices=["approve", "revise"],
        default="approve",
    )

    if decision == "approve":
        ci = CIAgent().run(task, all_files, summary)
        if ci["status"] == "committed":
            _clear_retry_context(task["id"])
            _db.move_task(task["id"], "done")
            console.print(f"[bold green]✓ Task {task['id']} done — {len(all_files)} file(s) committed.[/bold green]")
            return True
        # tox failed or commit failed — save current files as retry context, put back to ready
        _save_retry_context(task["id"], all_files)
        fresh = _db.get_task(task["id"])
        _db.append_review_feedback(task["id"], fresh, {
            "issues": [f"CI failed:\n{ci.get('output', ci.get('status'))}"],
            "overall_comment": "Tests failed after PM approval — fix and retry.",
        })
        _db.move_task(task["id"], "ready")
        console.print("[yellow]  status → ready  (CI failed — see task description)[/yellow]")
        return False

    # PM wants revision despite reviewer approval — save current files as retry context
    note = Prompt.ask("Revision instructions")
    _save_retry_context(task["id"], files)
    fresh = _db.get_task(task["id"])
    _db.append_review_feedback(task["id"], fresh, {
        "issues": [f"PM revision request: {note}"],
        "overall_comment": "PM requested changes after reviewer approval.",
    })
    _db.move_task(task["id"], "ready")
    console.print("[yellow]  status → ready  (PM revision requested)[/yellow]")
    return False


# ── Interactive session ───────────────────────────────────────────────────────

def session() -> None:
    _BACKEND_COLOR = {"claude-code": "magenta", "openrouter": "cyan", "ollama": "yellow"}

    def _fmt(name: str) -> str:
        s = config.step(name)
        color = _BACKEND_COLOR.get(s["backend"], "white")
        return f"[{color}]{s['backend']}[/{color}]  {s['model']}"

    console.print(Panel(
        "[bold cyan]Habr Agentic Pipeline — Dev Team[/bold cyan]\n"
        "You are the [bold]Tech / Project Manager[/bold]\n\n"
        f"  architect  : {_fmt('architect')}\n"
        f"  developer  : {_fmt('developer')}\n"
        f"  reviewer   : {_fmt('reviewer')}\n"
        f"  tester     : {_fmt('tester')}\n"
        f"  ci         : {_fmt('ci')}\n\n"
        f"  dashboard  : {config.DASHBOARD_URL}  project {config.DASHBOARD_PROJECT_ID}",
        border_style="cyan",
    ))

    while True:
        console.print(
            "\n[dim]Commands: (b)oard  (p)ick  run <id>  backlog  (q)uit[/dim]"
        )
        cmd = Prompt.ask("[bold]PM[/bold]").strip().lower()

        if cmd in ("q", "quit", "exit"):
            break
        elif cmd in ("b", "board"):
            show_board()
        elif cmd == "backlog":
            show_board(status_filter="backlog")
        elif cmd in ("p", "pick"):
            task = pick_task()
            if task:
                run_task(task)
        elif cmd.startswith("run "):
            try:
                tid  = int(cmd.split(None, 1)[1])
                task = _db.get_task(tid)
                run_task(task)
            except Exception as e:
                console.print(f"[red]{e}[/red]")
        else:
            console.print("[dim]board | pick | run <id> | backlog | quit[/dim]")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _print_task_header(task: dict, role: str) -> None:
    pc = _PRIORITY_COLOR.get(task["priority"], "white")
    console.print(
        f"\n[bold]#{task['id']}[/bold] {task['title']}  "
        f"[{pc}]{task['priority']}[/{pc}]"
    )
    if task.get("description"):
        console.print(f"[dim]{task['description'][:200]}[/dim]")
    console.print(f"→ agent: [cyan]{role}[/cyan]")


def _preview_file(f: dict) -> None:
    ext = Path(f["path"]).suffix.lstrip(".") or "python"
    lang_map = {"py": "python", "ts": "typescript", "tsx": "tsx", "md": "markdown"}
    lang = lang_map.get(ext, ext)
    console.print(Syntax(f["content"], lang, line_numbers=True, theme="monokai"))


def _write_files(files: list[dict]) -> None:
    for f in files:
        p = config.ROOT / f["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f["content"], encoding="utf-8")
        console.print(f"  [green]wrote[/green] {f['path']}")


# ── Retry context persistence ─────────────────────────────────────────────────

def _save_retry_context(task_id: int, files: list[dict]) -> None:
    """Persist the developer's output files so the next retry can start from them."""
    if not config.RETRY_WITH_CONTEXT:
        return
    retry_dir = config.RETRY_DIR / str(task_id)
    retry_dir.mkdir(parents=True, exist_ok=True)
    (retry_dir / "files.json").write_text(
        json.dumps(files, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    console.print(f"[dim]  retry context saved ({len(files)} file(s))[/dim]")


def _load_retry_context(task_id: int) -> list[dict] | None:
    """Load persisted files from the previous attempt, if any."""
    if not config.RETRY_WITH_CONTEXT:
        return None
    p = config.RETRY_DIR / str(task_id) / "files.json"
    if not p.exists():
        return None
    try:
        files = json.loads(p.read_text(encoding="utf-8"))
        console.print(f"[dim]  retry context loaded ({len(files)} file(s) from previous attempt)[/dim]")
        return files
    except Exception:
        return None


def _clear_retry_context(task_id: int) -> None:
    """Remove persisted retry context after a task is done or permanently failed."""
    retry_dir = config.RETRY_DIR / str(task_id)
    if retry_dir.exists():
        import shutil
        shutil.rmtree(retry_dir, ignore_errors=True)
