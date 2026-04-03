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
import shutil
import time
import traceback
from pathlib import Path

from rich.panel import Panel
from rich.rule import Rule

import config
from agents import ClaudeAgent, DevAgent, PMAgent, TestAgent
from clients import DashboardClient
from core import get_role_for_task
from dtypes import Action, LabelPrefix, Status

_db = DashboardClient(config.DASHBOARD_URL, config.DASHBOARD_PROJECT_ID)

_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# Populated at loop start — maps agent slug → dashboard agent id
_agent_id_cache: dict[str, int] = {}


def _refresh_agent_cache() -> None:
    """Refresh the slug→id map from the dashboard. Best-effort."""
    global _agent_id_cache
    try:
        _agent_id_cache = _db.get_agent_ids()
    except Exception:
        pass


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_step(task_id: int) -> None:
    """Process a single step for the given task and return. Used by the step CLI command."""
    _refresh_agent_cache()
    task = _db.get_task(task_id)
    action = _get_action(task)
    if action is None:
        config.console.print(
            f"[yellow]Task #{task_id} has no action label (action:todo or action:review). "
            f"Nothing to do.[/yellow]"
        )
        return
    if action == "await-human":
        config.console.print(
            f"[yellow]Task #{task_id} is paused at a human gate.[/yellow]\n"
            f"  python main.py review {task_id}   — inspect output\n"
            f"  python main.py approve {task_id}  — continue pipeline\n"
            f"  python main.py reject {task_id} \"feedback\" — retry with feedback"
        )
        return
    active_statuses = (Status.ARCHITECT, Status.DEVELOP, Status.TESTING)
    if task["status"] not in active_statuses:
        config.console.print(
            f"[yellow]Task #{task_id} is in '{task['status']}' — "
            f"must be in {active_statuses} to run a step.[/yellow]"
        )
        return
    _process_task(task)


def run_loop(poll_interval: int = config.EVENT_LOOP_POLL_INTERVAL) -> None:
    """Synchronous polling loop — process one task at a time, forever."""
    console = config.console
    console.print(Panel(
        "[bold cyan]Autonomous Event Loop[/bold cyan]\n"
        f"  Poll interval: {poll_interval}s\n"
        f"  Max retries:   {config.MAX_TASK_RETRIES}\n"
        f"  Dashboard:     {config.DASHBOARD_URL}  project {config.DASHBOARD_PROJECT_ID}",
        border_style="cyan",
    ))

    _refresh_agent_cache()

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
    """Return the highest-priority task that has an action label.

    Tasks with action:await-human are recognised but skipped — they are
    blocked until the developer runs approve/reject from the CLI.
    """
    tasks = _db.get_tasks()
    active_statuses = (Status.ARCHITECT, Status.DEVELOP, Status.TESTING)
    actionable = [
        t for t in tasks
        if _get_action(t) not in (None, "await-human") and t["status"] in active_statuses
    ]
    pending_human = [
        t for t in tasks
        if _get_action(t) == "await-human" and t["status"] in active_statuses
    ]
    if pending_human:
        ids = ", ".join(f"#{t['id']}" for t in pending_human)
        config.console.print(
            f"[dim]  {len(pending_human)} task(s) awaiting human review: {ids}  "
            f"→ python main.py pending[/dim]"
        )
    if not actionable:
        _check_parent_completions(tasks)
        return None

    actionable.sort(key=lambda t: _PRIORITY_ORDER.get(t["priority"], 4))
    return actionable[0]


def _get_action(task: dict) -> str | None:
    """Extract the action label from a task: todo, review, await-human, or None."""
    for label in task.get("labels", []):
        if label == Action.TODO:
            return "todo"
        if label == Action.REVIEW:
            return "review"
        if label == Action.AWAIT_HUMAN:
            return "await-human"
    return None


# ── Dispatch ──────────────────────────────────────────────────────────────────

def _process_task(task: dict) -> None:
    """Dispatch a task based on its status + action label."""
    console = config.console
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
        _add_label(task, f"{LabelPrefix.ERROR}max-retries")
        _db.move_task(tid, Status.FAILED)
        return

    _open_run_ids: list[int] = []
    _orig_create_run = _db.create_run

    def _tracked_create_run(task_id, agent_id, pipeline_type="dev_team"):
        run_id = _orig_create_run(task_id, agent_id, pipeline_type)
        if run_id >= 0:
            _open_run_ids.append(run_id)
        return run_id

    def _tracked_update_run(run_id, *args, **kwargs):
        if run_id in _open_run_ids:
            _open_run_ids.remove(run_id)
        return _db.__class__.update_run(_db, run_id, *args, **kwargs)

    _db.create_run = _tracked_create_run
    _db.update_run = _tracked_update_run

    try:
        if status == Status.ARCHITECT and action == "todo":
            _handle_architect_todo(task)
        elif status == Status.ARCHITECT and action == "review":
            _handle_architect_review(task)
        elif status == Status.DEVELOP and action == "todo":
            _handle_develop_todo(task)
        elif status == Status.DEVELOP and action == "review":
            _handle_develop_review(task)
        elif status == Status.TESTING and action == "todo":
            _handle_testing_todo(task)
        elif status == Status.TESTING and action == "review":
            _handle_testing_review(task)
        else:
            console.print(f"[yellow]Unhandled state: {status} + {action}[/yellow]")
    except Exception as exc:
        console.print(f"[red]Exception processing #{tid}: {exc}[/red]")
        _save_error_log(task, exc)
        for run_id in _open_run_ids:
            _db.__class__.update_run(_db, run_id, "failed", error_message=str(exc))
        _replace_action(task, None)
        _add_label(task, f"{LabelPrefix.ERROR}exception")
        _db.move_task(tid, Status.FAILED)
    finally:
        _db.create_run = _orig_create_run
        _db.update_run = _db.__class__.update_run.__get__(_db)


# ── Human gate ───────────────────────────────────────────────────────────────

def _apply_human_gate(task: dict, gate_name: str) -> bool:
    """If the human gate for this stage is enabled, pause the task and return True.

    The caller must return immediately when this returns True — the task is now
    blocked at action:await-human until the developer runs approve/reject.
    Returns False when the gate is off so the caller proceeds normally.
    """
    if config.HUMAN_GATES.get(gate_name, False):
        _replace_action(task, Action.AWAIT_HUMAN)
        config.console.print(
            f"\n[bold yellow]⏸  Human gate '{gate_name}'[/bold yellow]  "
            f"— task [bold]#{task['id']}[/bold] paused.\n"
            f"  [dim]python main.py review {task['id']}[/dim]   inspect output + spec\n"
            f"  [dim]python main.py approve {task['id']}[/dim]  continue to next stage\n"
            f"  [dim]python main.py reject {task['id']} \"feedback\"[/dim]  retry with your notes"
        )
        return True
    return False


# ── Handlers ──────────────────────────────────────────────────────────────────

def _handle_architect_todo(task: dict) -> None:
    """Run ClaudeAgent, save output, set action:review for PM."""
    console = config.console
    run_id = _db.create_run(task["id"], _agent_id_cache.get("architect:design"), "architect")
    result = ClaudeAgent("architect:design").run(task)

    if not result or not result.files:
        console.print("[red]Architect produced no output.[/red]")
        _db.update_run(run_id, "failed", error_message="No output produced")
        _increment_retry(task)
        _replace_action(task, Action.TODO)
        return

    _save_context(task["id"], "architect", result.model_dump())
    _db.update_run(
        run_id, "completed",
        output_summary=f"{len(result.files)} file(s): {(result.summary or '')[:200]}",
        output_payload=result.model_dump(),
    )
    _db.log_event(task["id"], "architect:output", {
        "file_count": len(result.files),
        "files": [f.path for f in result.files],
        "subtask_count": len(result.subtasks),
        "subtasks": [s.title for s in result.subtasks],
        "summary": result.summary,
    })
    if _apply_human_gate(task, "architect_output"):
        return
    _replace_action(task, Action.REVIEW)
    console.print(f"[green]Architect done — {len(result.files)} file(s). Awaiting PM review.[/green]")


def _handle_architect_review(task: dict) -> None:
    """PM reviews architect output. Approve → create subtasks. Reject → retry."""
    console = config.console
    run_id = _db.create_run(task["id"], _agent_id_cache.get("pm:architect-review"), "pm-review")
    ctx = _load_context(task["id"], "architect")
    if not ctx:
        console.print("[red]No architect context found. Resetting to action:todo.[/red]")
        _db.update_run(run_id, "failed", error_message="No architect context found")
        _replace_action(task, Action.TODO)
        return

    files = ctx["files"]
    subtasks = ctx.get("subtasks", [])
    summary = ctx.get("summary", "")

    pm = PMAgent()
    decision = pm.run_architect_review(task, files, subtasks, summary)

    if decision.approved:
        mods = decision.subtask_modifications
        for mod in mods:
            idx = mod.get("index", -1)
            if 0 <= idx < len(subtasks):
                if "title" in mod:
                    subtasks[idx]["title"] = mod["title"]
                if "description" in mod:
                    subtasks[idx]["description"] = mod["description"]

        subtask_ids = []
        for position, st in enumerate(subtasks):
            sid = _db.create_task(
                title=st["title"],
                description=st["description"],
                status=Status.DEVELOP,
                priority=st.get("priority", task["priority"]),
                labels=st.get("labels", ["developer"]) + [Action.TODO],
                parent_task_id=task["id"],
                queue_position=position,
            )
            subtask_ids.append(sid)
            console.print(f"  [green]Created subtask #{sid} (queue pos {position}):[/green] {st['title']}")

        for st_id in subtask_ids:
            _save_context(st_id, "skeleton_files", files)

        desc = task.get("description", "")
        subtask_lines = "\n".join(f"- #{sid}" for sid in subtask_ids)
        _db.update_task(task["id"], {
            "description": f"{desc}\n\n## Subtasks\n{subtask_lines}",
        })

        _replace_action(task, None)
        _db.update_run(run_id, "completed", output_summary=f"Approved — {len(subtask_ids)} subtask(s) created")
        _db.log_event(task["id"], "pm:architect_review", {
            "approved": True,
            "subtasks_created": subtask_ids,
        })
        console.print(f"[bold green]PM approved — {len(subtask_ids)} subtask(s) created.[/bold green]")
    else:
        feedback = decision.feedback or "No specific feedback."
        _append_feedback(task, feedback, "PM rejected architect output")
        _increment_retry(task)
        _replace_action(task, Action.TODO)
        _db.update_run(run_id, "completed", output_summary=f"Rejected: {feedback[:200]}")
        _db.log_event(task["id"], "pm:architect_review", {
            "approved": False,
            "feedback": feedback,
            "retry": _get_retry_count(task),
        })
        console.print("[yellow]PM rejected architect output. Retrying.[/yellow]")


def _handle_develop_todo(task: dict) -> None:
    """Run DevAgent on a development task. Set action:review when done."""
    console = config.console
    role = get_role_for_task(task) or "developer"
    run_id = _db.create_run(task["id"], _agent_id_cache.get(role) or _agent_id_cache.get("developer:implement"), "developer")

    skeleton_files = _load_context(task["id"], "skeleton_files")
    previous_files = _load_context(task["id"], "previous_files")

    def _save_developer_loop(messages: list[dict]) -> None:
        _db.log_event(task["id"], "react_loop:developer", _compact_messages(messages))

    result = DevAgent(role).run(
        task,
        feedback="",
        skeleton_files=skeleton_files if not previous_files else None,
        previous_files=previous_files,
        on_loop_complete=_save_developer_loop,
    )

    if not result or not result.files:
        console.print("[red]Developer produced no output.[/red]")
        _db.update_run(run_id, "failed", error_message="No output produced")
        _increment_retry(task)
        _replace_action(task, Action.TODO)
        return

    _save_context(task["id"], "developer", result.model_dump())
    _db.update_run(
        run_id, "completed",
        output_summary=f"{len(result.files)} file(s): {(result.summary or '')[:200]}",
        output_payload=result.model_dump(),
    )
    _db.log_event(task["id"], "developer:output", {
        "file_count": len(result.files),
        "files": [f.path for f in result.files],
        "summary": result.summary,
        "had_previous_files": _load_context(task["id"], "previous_files") is not None,
    })
    if _apply_human_gate(task, "develop_output"):
        return
    _replace_action(task, Action.REVIEW)
    console.print(f"[green]Developer done — {len(result.files)} file(s). Awaiting PM review.[/green]")


def _handle_develop_review(task: dict) -> None:
    """PM reviews developer output. Approve → testing. Reject → retry."""
    console = config.console
    run_id = _db.create_run(task["id"], _agent_id_cache.get("architect:dev-review"), "code-review")
    ctx = _load_context(task["id"], "developer")
    if not ctx:
        console.print("[red]No developer context found. Resetting to action:todo.[/red]")
        _db.update_run(run_id, "failed", error_message="No developer context found")
        _replace_action(task, Action.TODO)
        return

    files = ctx["files"]
    summary = ctx.get("summary", "")

    reviewer_result = ClaudeAgent("architect:dev-review").run_dev_review(task, files, summary)

    if not reviewer_result.approved:
        _save_context(task["id"], "previous_files", files)
        issues = reviewer_result.issues
        comment = reviewer_result.overall_comment
        _db.update_run(run_id, "completed", output_summary=f"Rejected: {len(issues)} issue(s). {comment[:150]}")
        _append_feedback(
            task,
            "\n".join(f"- {i}" for i in issues) + f"\n\nOverall: {comment}",
            "Code reviewer rejected",
        )
        _db.log_event(task["id"], "code_reviewer:rejected", {
            "issues": issues,
            "overall_comment": comment,
            "retry": _get_retry_count(task) + 1,
        })
        _increment_retry(task)
        _replace_action(task, Action.TODO)
        console.print(f"[yellow]Reviewer rejected — {len(issues)} issue(s). Retrying.[/yellow]")
        return

    _db.update_run(run_id, "completed", output_summary="Code review passed")

    pm_run_id = _db.create_run(task["id"], _agent_id_cache.get("pm:dev-review"), "pm-review")
    pm = PMAgent()
    decision = pm.run_developer_review(task, files, summary)

    if decision.approved:
        _db.move_task(task["id"], Status.TESTING)
        _replace_action(task, Action.TODO)
        _db.update_run(pm_run_id, "completed", output_summary="Approved — moving to testing")
        _db.log_event(task["id"], "pm:dev_review", {"approved": True})
        console.print("[bold green]PM approved developer output. Moving to testing.[/bold green]")
    else:
        _save_context(task["id"], "previous_files", files)
        feedback = decision.feedback or "No specific feedback."
        _append_feedback(task, feedback, "PM rejected developer output")
        _increment_retry(task)
        _replace_action(task, Action.TODO)
        _db.update_run(pm_run_id, "completed", output_summary=f"Rejected: {feedback[:200]}")
        _db.log_event(task["id"], "pm:dev_review", {
            "approved": False,
            "feedback": feedback,
            "retry": _get_retry_count(task),
        })
        console.print("[yellow]PM rejected developer output. Retrying.[/yellow]")


def _handle_testing_todo(task: dict) -> None:
    """Run TestAgent + CIAgent. Set action:review for PM."""
    console = config.console
    run_id = _db.create_run(task["id"], _agent_id_cache.get("tester:unit-tests"), "testing")
    ctx = _load_context(task["id"], "developer")
    if not ctx:
        console.print("[red]No developer context for testing. Moving back to develop.[/red]")
        _db.update_run(run_id, "failed", error_message="No developer context for testing")
        _db.move_task(task["id"], Status.DEVELOP)
        _replace_action(task, Action.TODO)
        return

    files = ctx["files"]
    summary = ctx.get("summary", "")

    def _save_tester_loop(messages: list[dict]) -> None:
        _db.log_event(task["id"], "react_loop:tester", _compact_messages(messages))

    test_result = TestAgent().run(task, files, on_loop_complete=_save_tester_loop)
    all_files = files + [f.model_dump() for f in test_result.files]

    ci_result = TestAgent().run_ci(task, all_files, summary)

    _save_context(task["id"], "testing", {
        "files": all_files,
        "ci_result": ci_result.model_dump(),
        "summary": summary,
    })

    _db.update_run(
        run_id, "completed",
        output_summary=f"CI: {ci_result.status}. {len(test_result.files)} test file(s).",
        output_payload=ci_result.model_dump(),
        logs_text=ci_result.output[:4000] if ci_result.output else None,
    )
    _db.log_event(task["id"], "tester:ci_result", {
        "ci_status": ci_result.status,
        "test_file_count": len(test_result.files),
        "test_files": [f.path for f in test_result.files],
        "sha": ci_result.sha,
        "ci_output_tail": (ci_result.output or "")[-500:],
    })
    if _apply_human_gate(task, "testing_output"):
        return
    _replace_action(task, Action.REVIEW)
    console.print(f"[green]Testing done — CI status: {ci_result.status}. Awaiting PM review.[/green]")


def _handle_testing_review(task: dict) -> None:
    """PM reviews test results. Approve → done. Reject → back to develop."""
    console = config.console
    run_id = _db.create_run(task["id"], _agent_id_cache.get("pm:testing-review"), "pm-review")
    ctx = _load_context(task["id"], "testing")
    if not ctx:
        console.print("[red]No testing context found. Resetting to action:todo.[/red]")
        _db.update_run(run_id, "failed", error_message="No testing context found")
        _replace_action(task, Action.TODO)
        return

    files = ctx["files"]
    ci_result_raw = ctx["ci_result"]
    summary = ctx.get("summary", "")
    tox_output = ci_result_raw.get("output", "") or ""

    pm = PMAgent()
    decision = pm.run_testing_review(task, files, tox_output, summary)

    if decision.approved:
        if ci_result_raw.get("status") == "committed":
            _db.log_event(task["id"], "pm:testing_review", {"approved": True, "sha": ci_result_raw.get("sha")})
            _clear_context(task["id"])
            _replace_action(task, None)
            _db.move_task(task["id"], Status.DONE)
            _db.update_run(run_id, "completed", output_summary=f"Approved — task done. SHA: {ci_result_raw.get('sha', '')}")
            console.print(f"[bold green]✓ Task #{task['id']} done![/bold green]")
            _update_claude_md(task, files, summary)
            PMAgent().run_analysis(task["id"])
            if task.get("parent_task_id"):
                _check_single_parent(task["parent_task_id"])
        else:
            _db.move_task(task["id"], Status.DEVELOP)
            _save_context(task["id"], "previous_files", files)
            _append_feedback(task, f"CI status: {ci_result_raw.get('status')}. {tox_output[-500:]}", "CI did not commit")
            _increment_retry(task)
            _replace_action(task, Action.TODO)
            _db.update_run(run_id, "completed", output_summary=f"Approved but CI status={ci_result_raw.get('status')}")
            _db.log_event(task["id"], "pm:testing_review", {
                "approved": False,
                "reason": "ci_not_committed",
                "ci_status": ci_result_raw.get("status"),
            })
            console.print("[yellow]PM approved but CI didn't commit. Back to develop.[/yellow]")
    else:
        _db.move_task(task["id"], Status.DEVELOP)
        _save_context(task["id"], "previous_files", [f for f in files if not f["path"].startswith("backend/tests/")])
        feedback = decision.feedback or ""
        _append_feedback(task, feedback, "PM rejected testing output")
        _increment_retry(task)
        _replace_action(task, Action.TODO)
        _db.update_run(run_id, "completed", output_summary=f"Rejected: {feedback[:200]}")
        _db.log_event(task["id"], "pm:testing_review", {
            "approved": False,
            "feedback": feedback,
            "retry": _get_retry_count(task),
        })
        console.print("[yellow]PM rejected testing output. Back to develop.[/yellow]")


# ── Parent completion check ───────────────────────────────────────────────────

def _check_parent_completions(all_tasks: list[dict]) -> None:
    """Check if any parent tasks should be completed (all subtasks done)."""
    console = config.console
    parent_ids = {
        t.get("parent_task_id")
        for t in all_tasks
        if t.get("parent_task_id") is not None
    }
    for pid in parent_ids:
        parent = next((t for t in all_tasks if t["id"] == pid), None)
        if parent and parent["status"] not in (Status.DONE, Status.FAILED):
            subtasks = [t for t in all_tasks if t.get("parent_task_id") == pid]
            if subtasks and all(t["status"] == Status.DONE for t in subtasks):
                _db.move_task(pid, Status.DONE)
                console.print(f"[bold green]✓ Parent task #{pid} completed (all subtasks done).[/bold green]")


def _check_single_parent(parent_task_id: int) -> None:
    """Check if a specific parent's subtasks are all done."""
    subtasks = _db.get_subtasks(parent_task_id)
    if subtasks and all(t["status"] == Status.DONE for t in subtasks):
        _db.move_task(parent_task_id, Status.DONE)
        config.console.print(f"[bold green]✓ Parent task #{parent_task_id} completed (all subtasks done).[/bold green]")


# ── Label helpers ─────────────────────────────────────────────────────────────

def _replace_action(task: dict, new_action: str | None) -> None:
    """Remove any existing action:* labels and optionally set a new one."""
    labels = [l for l in task.get("labels", []) if not l.startswith(Action.PREFIX)]
    if new_action:
        labels.append(new_action)
    _db.set_labels(task["id"], labels)
    task["labels"] = labels


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
        m = re.match(rf"^{re.escape(LabelPrefix.RETRY)}(\d+)$", label)
        if m:
            return int(m.group(1))
    return 0


def _increment_retry(task: dict) -> None:
    """Increment the retry counter label."""
    count = _get_retry_count(task) + 1
    labels = [l for l in task.get("labels", []) if not l.startswith(LabelPrefix.RETRY)]
    labels.append(f"{LabelPrefix.RETRY}{count}")
    _db.set_labels(task["id"], labels)
    task["labels"] = labels
    config.console.print(f"[dim]  retry count: {count}/{config.MAX_TASK_RETRIES}[/dim]")


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
    d = config.CONTEXT_DIR / str(task_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def _update_claude_md(task: dict, files: list[dict], summary: str) -> None:
    """Append a brief task-completion note to the target project's CLAUDE.md."""
    claude_md = config.ROOT / "CLAUDE.md"
    if not claude_md.exists():
        return
    file_list = "\n".join(f"  - {f['path']}" for f in files[:12])
    if len(files) > 12:
        file_list += f"\n  - … ({len(files) - 12} more)"
    note = (
        f"\n\n<!-- dev_team: task #{task['id']} completed -->\n"
        f"## [{task['title']}] — done\n"
        f"{summary.strip()}\n\n"
        f"Files changed:\n{file_list}\n"
    )
    try:
        with claude_md.open("a", encoding="utf-8") as f:
            f.write(note)
        config.console.print(f"[dim]  CLAUDE.md updated with task #{task['id']} notes.[/dim]")
    except Exception as e:
        config.console.print(f"[yellow]  Could not update CLAUDE.md: {e}[/yellow]")


def _save_error_log(task: dict, exc: Exception) -> None:
    """Save exception traceback to context dir."""
    path = _context_dir(task["id"]) / "error.log"
    path.write_text(traceback.format_exc(), encoding="utf-8")


def _compact_messages(messages: list[dict], max_content: int = 2000) -> dict:
    """Truncate message content so the payload fits in an activity event."""
    compacted = []
    for msg in messages:
        m = {"role": msg.get("role", "")}
        content = msg.get("content", "")
        if isinstance(content, str):
            m["content"] = content[:max_content] + f" …[+{len(content) - max_content}]" if len(content) > max_content else content
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            m["tool_calls"] = [
                {
                    "function": {
                        "name": tc.get("function", {}).get("name"),
                        "arguments": (
                            tc["function"]["arguments"][:max_content] + " …[truncated]"
                            if isinstance(tc.get("function", {}).get("arguments"), str)
                            and len(tc["function"]["arguments"]) > max_content
                            else tc.get("function", {}).get("arguments")
                        ),
                    }
                }
                for tc in tool_calls
            ]
        compacted.append(m)
    return {"round_count": sum(1 for m in messages if m.get("role") == "assistant"), "messages": compacted}
