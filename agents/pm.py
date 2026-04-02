"""PMAgent — autonomous Project Manager that reviews agent output and makes decisions."""
import json
from rich.panel import Panel
from rich.rule import Rule

import config
from clients import DashboardClient
from core import create_client, parse_json_response, stream_chat_with_display
from dtypes import ReviewResult
from prompts import (
    PM_ARCHITECT_REVIEW,
    PM_ARCHITECT_SUBTASKS_HEADER,
    PM_ARCHITECT_USER_PROMPT,
    PM_DEVELOPER_REVIEW,
    PM_DEVELOPER_USER_PROMPT,
    PM_TESTING_REVIEW,
    PM_TESTING_USER_PROMPT,
    PM_USER_STORY_SYSTEM_PROMPT,
)
from prompts.pm_analysis import PM_ANALYSIS_SYSTEM_PROMPT, PM_ANALYSIS_USER_PROMPT_HEADER

_db = DashboardClient(config.DASHBOARD_URL, config.DASHBOARD_PROJECT_ID)


class PMAgent:
    """Autonomous PM that reviews agent output at each pipeline stage."""

    def __init__(self):
        self.client = create_client("pm")

    # ── User story ────────────────────────────────────────────────────────────

    def create_user_story(self, raw_requirement: str) -> dict:
        """Turn a raw requirement string into a structured user story dict (pm:user-story role)."""
        config.print_agent_rule("PM — User Story", "pm")
        try:
            final_resp, _ = stream_chat_with_display(
                self.client,
                messages=[
                    {"role": "system", "content": PM_USER_STORY_SYSTEM_PROMPT},
                    {"role": "user", "content": raw_requirement},
                ],
                temperature=0.3,
                timeout=120,
            )
            content = final_resp.get("message", {}).get("content", "")
            return parse_json_response(content)
        except Exception as e:
            config.console.print(f"[red]  PM user-story error: {e}[/red]")
            return {}

    # ── Post-mortem analysis (pm:analysis role) ───────────────────────────────

    def run_analysis(self, task_id: int) -> None:
        """Fetch events for task_id, analyse, and persist prompt improvement suggestions."""
        console = config.console
        config.print_agent_rule("PM — Post-mortem Analysis", "pm")

        try:
            task = _db.get_task(task_id)
            events = _db.get_task_events(task_id)

            if not events:
                console.print("[dim]  No events to analyse — skipping.[/dim]")
                return

            final_resp, _ = stream_chat_with_display(
                self.client,
                messages=[
                    {"role": "system", "content": PM_ANALYSIS_SYSTEM_PROMPT},
                    {"role": "user", "content": self._build_analysis_prompt(task, events)},
                ],
                temperature=0.2,
                timeout=300,
            )
            content = final_resp.get("message", {}).get("content", "")
            raw = parse_json_response(content)
            suggestions = raw.get("suggestions", [])

            if not suggestions:
                console.print("[dim]  No prompt improvements identified.[/dim]")
                return

            saved = 0
            for s in suggestions:
                result = _db.create_suggestion(
                    task_id=task_id,
                    agent_role=s.get("agent_role", "unknown"),
                    issue_pattern=s.get("issue_pattern", ""),
                    suggested_instruction=s.get("suggested_instruction", ""),
                    evidence=s.get("evidence", []),
                )
                if result:
                    saved += 1

            console.print(Panel(
                f"[bold cyan]{saved} suggestion(s) stored[/bold cyan]\n"
                + "\n".join(
                    f"  [{s.get('agent_role')}] {s.get('issue_pattern', '')[:80]}"
                    for s in suggestions
                ),
                border_style="cyan",
                title="PM — Post-mortem",
            ))

        except Exception as e:
            console.print(f"[yellow]  PM analysis error (non-fatal): {e}[/yellow]")

    @staticmethod
    def _build_analysis_prompt(task: dict, events: list[dict]) -> str:
        lines = [PM_ANALYSIS_USER_PROMPT_HEADER.format(
            task_id=task["id"],
            title=task["title"],
            event_count=len(events),
        )]
        for ev in events:
            payload_str = json.dumps(ev.get("payload", {}), ensure_ascii=False)
            if len(payload_str) > 600:
                payload_str = payload_str[:600] + " ..."
            lines.append(f"[{ev.get('created_at', '?')}] {ev['event_type']}: {payload_str}")
        return "\n".join(lines)

    # ── Stubs (future roles) ──────────────────────────────────────────────────

    def run_task_analysis(self, task: dict) -> ReviewResult:
        raise NotImplementedError("pm:task-analysis is not implemented yet")

    def run_requirement_analysis(self, task: dict) -> ReviewResult:
        raise NotImplementedError("pm:requirement-analysis is not implemented yet")

    def run_task_closed_analysis(self, task: dict) -> ReviewResult:
        raise NotImplementedError("pm:task-closed-analysis is not implemented yet")

    # ── Review methods ────────────────────────────────────────────────────────

    def run_architect_review(
        self,
        task: dict,
        files: list[dict],
        subtasks: list[dict],
        summary: str,
    ) -> ReviewResult:
        """Review architect skeleton files and proposed subtasks."""
        prompt = self._build_architect_prompt(task, files, subtasks, summary)
        return self._run_review("Architect Review", PM_ARCHITECT_REVIEW, prompt)

    def run_developer_review(
        self,
        task: dict,
        files: list[dict],
        summary: str,
    ) -> ReviewResult:
        """Review developer implementation."""
        prompt = self._build_developer_prompt(task, files, summary)
        return self._run_review("Developer Review", PM_DEVELOPER_REVIEW, prompt)

    def run_testing_review(
        self,
        task: dict,
        files: list[dict],
        tox_output: str,
        summary: str,
    ) -> ReviewResult:
        """Final review of test results before marking done."""
        prompt = self._build_testing_prompt(task, files, tox_output, summary)
        return self._run_review("Testing Review", PM_TESTING_REVIEW, prompt)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run_review(self, title: str, system_prompt: str, user_prompt: str) -> ReviewResult:
        console = config.console
        config.print_agent_rule(f"PM — {title}", "pm")

        try:
            final_resp, _ = stream_chat_with_display(
                self.client,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                timeout=300,
            )
            content = final_resp.get("message", {}).get("content", "")
            raw = parse_json_response(content)
            result = ReviewResult(**raw)
        except Exception as e:
            console.print(f"[red]  PM review error: {e}[/red]")
            result = ReviewResult(approved=False, feedback=f"PM review failed: {e}")

        _print_decision(title, result)
        return result

    @staticmethod
    def _build_architect_prompt(
        task: dict, files: list[dict], subtasks: list[dict], summary: str,
    ) -> str:
        lines = [PM_ARCHITECT_USER_PROMPT.format(
            title=task["title"],
            priority=task["priority"],
            description=task.get("description", "No description."),
            summary=summary,
            count=len(files),
        )]
        for f in files:
            lines.append(f"=== {f['path']} ===")
            content = f["content"]
            if len(content) > 3000:
                lines.append(content[:3000])
                lines.append(f"[... {len(content) - 3000} chars truncated ...]")
            else:
                lines.append(content)
            lines.append("")

        if subtasks:
            lines.append(PM_ARCHITECT_SUBTASKS_HEADER.format(count=len(subtasks)))
            lines.append("")
            for i, st in enumerate(subtasks):
                lines.append(f"  [{i}] {st['title']}")
                lines.append(f"      {st.get('description', '')}")
                lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _build_developer_prompt(
        task: dict, files: list[dict], summary: str,
    ) -> str:
        lines = [PM_DEVELOPER_USER_PROMPT.format(
            title=task["title"],
            priority=task["priority"],
            description=task.get("description", "No description."),
            summary=summary,
            count=len(files),
        )]
        for f in files:
            lines.append(f"=== {f['path']} ===")
            content = f["content"]
            if len(content) > 4000:
                lines.append(content[:4000])
                lines.append(f"[... {len(content) - 4000} chars truncated ...]")
            else:
                lines.append(content)
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _build_testing_prompt(
        task: dict, files: list[dict], tox_output: str, summary: str,
    ) -> str:
        tox_lines = "\n".join(tox_output.strip().splitlines()[-80:]) if tox_output else "(no output)"
        lines = [PM_TESTING_USER_PROMPT.format(
            title=task["title"],
            description=task.get("description", "No description."),
            summary=summary,
            tox_output=tox_lines,
            count=len(files),
        )]
        for f in files:
            lines.append(f"  - {f['path']}  ({len(f['content'])} chars)")
        return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _print_decision(title: str, result: ReviewResult) -> None:
    console = config.console
    console.print(Rule())
    if result.approved:
        console.print(Panel(
            "[bold green]✓ APPROVED[/bold green]",
            border_style="green",
            title=f"PM — {title}",
        ))
    else:
        feedback = result.feedback or "No feedback provided."
        console.print(Panel(
            f"[bold red]✗ REJECTED[/bold red]\n\n{feedback}",
            border_style="red",
            title=f"PM — {title}",
        ))
