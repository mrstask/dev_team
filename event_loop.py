"""Autonomous event loop — polls the dashboard and dispatches tasks to agents.

Replaces the interactive orchestrator session. Runs one task at a time in a
synchronous polling loop. Each task is dispatched based on its status and
action label (action:todo or action:review).

State machine:
  backlog                     → (manual kick) → architect + action:todo
  architect + action:todo     → ClaudeAgent   → architect + action:review
  architect + action:review   → PMAgent       → develop subtasks (action:todo) or reject
  develop   + action:todo     → DevAgent      → develop + action:review
  develop   + action:review   → PMAgent       → testing + action:todo or reject
  testing   + action:todo     → Reviewer+Tester+CI → testing + action:review
  testing   + action:review   → PMAgent       → done or reject back to develop
"""
import json
import re
import time
import traceback
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule

import config
from agent import DevAgent
from ci_agent import CIAgent
from claude_agent import ClaudeAgent
from dashboard_client import DashboardClient
from pm_agent import PMAgent
from reviewer import ReviewerAgent
from roles import get_role_for_task
from tester import TestAgent

console = Console()

_db = DashboardClient(config.DASHBOARD_URL, config.DASHBOARD_PROJECT_ID)

_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_loop(poll_interval: int = config.EVENT_LOOP_POLL_INTERVAL) -> None:
    """Synchronous polling loop — process one task at a time, forever."""
    console.print(Panel(
        "[bold cyan]Autonomous Event Loop[/bold cyan]\n"
        f"  Poll interval: {poll_interval}s\n"
        f"  Max retries:   {config.MAX_TASK_RETRIES}\n"
        f"  Dashboard:     {config.DASHBOARD_URL}  project {config.DASHBOARD_PROJECT_ID}",
        border_style="cyan",
    ))

    while True:
        try:
            task = _fetch_next_actionable()
            if task:
                _process_task(task)
            else:
                console.print("[dim]No actionable tasks. Sleeping...[/dim]")
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            console.print("\n[yellow]Event loop stopped by user.[/yellow]")
            break
        except Exception as exc:
            console.print(f"[red]Loop error: {exc}[/red]")
            time.sleep(poll_interval)


# ── Fetch ─────────────────────────────────────────────────────────────────────

def _fetch_next_actionable() -> dict | None:
    """Return the highest-priority task that has an action label."""
    tasks = _db.get_tasks()
    actionable = [
        t for t in tasks
        if _get_action(t) is not None and t["status"] in ("architect", "develop", "testing")
    ]
    if not actionable:
        # Also check for parent tasks whose subtasks are all done
        _check_parent_completions(tasks)
        return None

    actionable.sort(key=lambda t: _PRIORITY_ORDER.get(t["priority"], 4))
    return actionable[0]


def _get_action(task: dict) -> str | None:
    """Extract the action label (todo or review) from a task, if any."""
    for label in task.get("labels", []):
        if label == "action:todo":
            return "todo"
        if label == "action:review":
            return "review"
    return None


# ── Dispatch ──────────────────────────────────────────────────────────────────

def _process_task(task: dict) -> None:
    """Dispatch a task based on its status + action label."""
    status = task["status"]
    action = _get_action(task)
    tid = task["id"]

    console.print(Rule(style="cyan"))
    console.print(
        f"[bold]#{tid}[/bold] {task['title']}  "
        f"[dim]{status}[/dim] + [dim]{action}[/dim]  "
        f"[{'red' if task['priority'] == 'critical' else 'yellow' if task['priority'] == 'high' else 'blue'}]"
        f"{task['priority']}[/]"
    )

    if _get_retry_count(task) >= config.MAX_TASK_RETRIES:
        console.print(f"[red]Task #{tid} exceeded max retries ({config.MAX_TASK_RETRIES}). Marking failed.[/red]")
        _replace_action(task, None)
        _add_label(task, "error:max-retries")
        _db.move_task(tid, "failed")
        return

    try:
        if status == "architect" and action == "todo":
            _handle_architect_todo(task)
        elif status == "architect" and action == "review":
            _handle_architect_review(task)
        elif status == "develop" and action == "todo":
            _handle_develop_todo(task)
        elif status == "develop" and action == "review":
            _handle_develop_review(task)
        elif status == "testing" and action == "todo":
            _handle_testing_todo(task)
        elif status == "testing" and action == "review":
            _handle_testing_review(task)
        else:
            console.print(f"[yellow]Unhandled state: {status} + {action}[/yellow]")
    except Exception as exc:
        console.print(f"[red]Exception processing #{tid}: {exc}[/red]")
        _save_error_log(task, exc)
        _replace_action(task, None)
        _add_label(task, "error:exception")
        _db.move_task(tid, "failed")


# ── Handlers ──────────────────────────────────────────────────────────────────

def _handle_architect_todo(task: dict) -> None:
    """Run ClaudeAgent, save output, set action:review for PM."""
    result = ClaudeAgent("architect").run(task)

    if not result or not result.get("files"):
        console.print("[red]Architect produced no output.[/red]")
        _increment_retry(task)
        _replace_action(task, "action:todo")  # retry
        return

    # Save architect context for PM review
    _save_context(task["id"], "architect", {
        "files": result["files"],
        "summary": result.get("summary", ""),
        "subtasks": result.get("subtasks", []),
    })

    _replace_action(task, "action:review")
    console.print(f"[green]Architect done — {len(result['files'])} file(s). Awaiting PM review.[/green]")


def _handle_architect_review(task: dict) -> None:
    """PM reviews architect output. Approve → create subtasks. Reject → retry."""
    ctx = _load_context(task["id"], "architect")
    if not ctx:
        console.print("[red]No architect context found. Resetting to action:todo.[/red]")
        _replace_action(task, "action:todo")
        return

    files = ctx["files"]
    subtasks = ctx.get("subtasks", [])
    summary = ctx.get("summary", "")

    pm = PMAgent()
    decision = pm.review_architect(task, files, subtasks, summary)

    if decision.get("approved"):
        # Apply any subtask modifications from PM
        mods = decision.get("subtask_modifications", [])
        for mod in mods:
            idx = mod.get("index", -1)
            if 0 <= idx < len(subtasks):
                if "title" in mod:
                    subtasks[idx]["title"] = mod["title"]
                if "description" in mod:
                    subtasks[idx]["description"] = mod["description"]

        # Create subtasks in dashboard
        subtask_ids = []
        for st in subtasks:
            sid = _db.create_task(
                title=st["title"],
                description=st["description"],
                status="develop",
                priority=st.get("priority", task["priority"]),
                labels=st.get("labels", ["developer"]) + ["action:todo"],
                parent_task_id=task["id"],
            )
            subtask_ids.append(sid)
            console.print(f"  [green]Created subtask #{sid}:[/green] {st['title']}")

        # Save skeleton files so developer subtasks can reference them
        for st_id in subtask_ids:
            _save_context(st_id, "skeleton_files", files)

        # Update parent task description with subtask references
        desc = task.get("description", "")
        subtask_lines = "\n".join(f"- #{sid}" for sid in subtask_ids)
        _db.update_task(task["id"], {
            "description": f"{desc}\n\n## Subtasks\n{subtask_lines}",
        })

        # Parent task: remove action label, wait for subtasks to complete
        _replace_action(task, None)
        console.print(f"[bold green]PM approved — {len(subtask_ids)} subtask(s) created.[/bold green]")
    else:
        # Rejected — send back to architect with feedback
        feedback = decision.get("feedback", "No specific feedback.")
        _append_feedback(task, feedback, "PM rejected architect output")
        _increment_retry(task)
        _replace_action(task, "action:todo")
        console.print(f"[yellow]PM rejected architect output. Retrying.[/yellow]")


def _handle_develop_todo(task: dict) -> None:
    """Run DevAgent on a development task. Set action:review when done."""
    # Load skeleton files from context (saved when architect was approved)
    skeleton_files = _load_context(task["id"], "skeleton_files")
    previous_files = _load_context(task["id"], "previous_files")

    role = get_role_for_task(task) or "developer"
    result = DevAgent(role).run(
        task,
        feedback="",
        skeleton_files=skeleton_files if not previous_files else None,
        previous_files=previous_files,
    )

    if not result or not result.get("files"):
        console.print("[red]Developer produced no output.[/red]")
        _increment_retry(task)
        _replace_action(task, "action:todo")
        return

    # Save developer output for PM review
    _save_context(task["id"], "developer", {
        "files": result["files"],
        "summary": result.get("summary", ""),
    })

    _replace_action(task, "action:review")
    console.print(f"[green]Developer done — {len(result['files'])} file(s). Awaiting PM review.[/green]")


def _handle_develop_review(task: dict) -> None:
    """PM reviews developer output. Approve → testing. Reject → retry."""
    ctx = _load_context(task["id"], "developer")
    if not ctx:
        console.print("[red]No developer context found. Resetting to action:todo.[/red]")
        _replace_action(task, "action:todo")
        return

    files = ctx["files"]
    summary = ctx.get("summary", "")

    # Run code reviewer first
    reviewer_result = ReviewerAgent().review(task, files, summary)

    if not reviewer_result.get("approved"):
        # Reviewer rejected — save files for retry
        _save_context(task["id"], "previous_files", files)
        issues = reviewer_result.get("issues", [])
        comment = reviewer_result.get("overall_comment", "")
        _append_feedback(
            task,
            "\n".join(f"- {i}" for i in issues) + f"\n\nOverall: {comment}",
            "Code reviewer rejected",
        )
        _increment_retry(task)
        _replace_action(task, "action:todo")
        console.print(f"[yellow]Reviewer rejected — {len(issues)} issue(s). Retrying.[/yellow]")
        return

    # Reviewer approved — now PM reviews
    pm = PMAgent()
    decision = pm.review_developer(task, files, summary)

    if decision.get("approved"):
        # Move to testing
        _db.move_task(task["id"], "testing")
        _replace_action(task, "action:todo")
        console.print("[bold green]PM approved developer output. Moving to testing.[/bold green]")
    else:
        # PM rejected — save files for retry, send back to developer
        _save_context(task["id"], "previous_files", files)
        feedback = decision.get("feedback", "No specific feedback.")
        _append_feedback(task, feedback, "PM rejected developer output")
        _increment_retry(task)
        _replace_action(task, "action:todo")
        console.print(f"[yellow]PM rejected developer output. Retrying.[/yellow]")


def _handle_testing_todo(task: dict) -> None:
    """Run TestAgent + CIAgent. Set action:review for PM."""
    ctx = _load_context(task["id"], "developer")
    if not ctx:
        console.print("[red]No developer context for testing. Moving back to develop.[/red]")
        _db.move_task(task["id"], "develop")
        _replace_action(task, "action:todo")
        return

    files = ctx["files"]
    summary = ctx.get("summary", "")

    # Generate tests
    test_files = TestAgent().generate_tests(task, files) or []
    all_files = files + test_files

    # Run CI (write files, tox, commit)
    ci_result = CIAgent().run(task, all_files, summary)

    # Save testing context for PM review
    _save_context(task["id"], "testing", {
        "files": all_files,
        "ci_result": ci_result,
        "summary": summary,
    })

    _replace_action(task, "action:review")
    status = ci_result.get("status", "unknown")
    console.print(f"[green]Testing done — CI status: {status}. Awaiting PM review.[/green]")


def _handle_testing_review(task: dict) -> None:
    """PM reviews test results. Approve → done. Reject → back to develop."""
    ctx = _load_context(task["id"], "testing")
    if not ctx:
        console.print("[red]No testing context found. Resetting to action:todo.[/red]")
        _replace_action(task, "action:todo")
        return

    files = ctx["files"]
    ci_result = ctx["ci_result"]
    summary = ctx.get("summary", "")
    tox_output = ci_result.get("output", "")

    pm = PMAgent()
    decision = pm.review_testing(task, files, tox_output, summary)

    if decision.get("approved"):
        if ci_result.get("status") == "committed":
            _clear_context(task["id"])
            _replace_action(task, None)
            _db.move_task(task["id"], "done")
            console.print(f"[bold green]✓ Task #{task['id']} done![/bold green]")
            # Check if parent task should also complete
            if task.get("parent_task_id"):
                _check_single_parent(task["parent_task_id"])
        else:
            # Tests might have passed in PM's view but CI didn't commit
            # Send back for another CI attempt
            _db.move_task(task["id"], "develop")
            _save_context(task["id"], "previous_files", files)
            _append_feedback(task, f"CI status: {ci_result.get('status')}. {tox_output[-500:]}", "CI did not commit")
            _increment_retry(task)
            _replace_action(task, "action:todo")
            console.print("[yellow]PM approved but CI didn't commit. Back to develop.[/yellow]")
    else:
        # PM rejected — back to develop
        _db.move_task(task["id"], "develop")
        _save_context(task["id"], "previous_files", [f for f in files if not f["path"].startswith("backend/tests/")])
        feedback = decision.get("feedback", "")
        _append_feedback(task, feedback, "PM rejected testing output")
        _increment_retry(task)
        _replace_action(task, "action:todo")
        console.print("[yellow]PM rejected testing output. Back to develop.[/yellow]")


# ── Parent completion check ───────────────────────────────────────────────────

def _check_parent_completions(all_tasks: list[dict]) -> None:
    """Check if any parent tasks should be completed (all subtasks done)."""
    parent_ids = {
        t.get("parent_task_id")
        for t in all_tasks
        if t.get("parent_task_id") is not None
    }
    for pid in parent_ids:
        parent = next((t for t in all_tasks if t["id"] == pid), None)
        if parent and parent["status"] not in ("done", "failed"):
            subtasks = [t for t in all_tasks if t.get("parent_task_id") == pid]
            if subtasks and all(t["status"] == "done" for t in subtasks):
                _db.move_task(pid, "done")
                console.print(f"[bold green]✓ Parent task #{pid} completed (all subtasks done).[/bold green]")


def _check_single_parent(parent_task_id: int) -> None:
    """Check if a specific parent's subtasks are all done."""
    subtasks = _db.get_subtasks(parent_task_id)
    if subtasks and all(t["status"] == "done" for t in subtasks):
        _db.move_task(parent_task_id, "done")
        console.print(f"[bold green]✓ Parent task #{parent_task_id} completed (all subtasks done).[/bold green]")


# ── Label helpers ─────────────────────────────────────────────────────────────

def _replace_action(task: dict, new_action: str | None) -> None:
    """Remove any existing action:* labels and optionally set a new one."""
    labels = [l for l in task.get("labels", []) if not l.startswith("action:")]
    if new_action:
        labels.append(new_action)
    _db.set_labels(task["id"], labels)
    task["labels"] = labels  # keep local copy in sync


def _add_label(task: dict, label: str) -> None:
    """Add a label to a task (no duplicates)."""
    labels = list(task.get("labels", []))
    if label not in labels:
        labels.append(label)
        _db.set_labels(task["id"], labels)
        task["labels"] = labels


# ── Retry tracking ────────────────────────────────────────────────────────────

def _get_retry_count(task: dict) -> int:
    """Get retry count from labels (retry:N)."""
    for label in task.get("labels", []):
        m = re.match(r"^retry:(\d+)$", label)
        if m:
            return int(m.group(1))
    return 0


def _increment_retry(task: dict) -> None:
    """Increment the retry counter label."""
    count = _get_retry_count(task) + 1
    labels = [l for l in task.get("labels", []) if not l.startswith("retry:")]
    labels.append(f"retry:{count}")
    _db.set_labels(task["id"], labels)
    task["labels"] = labels
    console.print(f"[dim]  retry count: {count}/{config.MAX_TASK_RETRIES}[/dim]")


# ── Feedback helpers ──────────────────────────────────────────────────────────

def _append_feedback(task: dict, feedback: str, source: str) -> None:
    """Append review/PM feedback to task description."""
    fresh = _db.get_task(task["id"])
    _db.append_review_feedback(task["id"], fresh, {
        "issues": [feedback] if feedback else [],
        "overall_comment": source,
    })


# ── Context persistence ──────────────────────────────────────────────────────

def _context_dir(task_id: int) -> Path:
    d = config.CONTEXT_DIR / str(task_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_context(task_id: int, key: str, data) -> None:
    """Save arbitrary JSON-serialisable data to the context store."""
    path = _context_dir(task_id) / f"{key}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_context(task_id: int, key: str):
    """Load context data. Returns None if not found."""
    path = config.CONTEXT_DIR / str(task_id) / f"{key}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _clear_context(task_id: int) -> None:
    """Remove all context for a completed task."""
    import shutil
    d = config.CONTEXT_DIR / str(task_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def _save_error_log(task: dict, exc: Exception) -> None:
    """Save exception traceback to context dir."""
    path = _context_dir(task["id"]) / "error.log"
    path.write_text(traceback.format_exc(), encoding="utf-8")
