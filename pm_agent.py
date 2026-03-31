"""PMAgent — autonomous Project Manager that reviews agent output and makes decisions."""
from rich.panel import Panel
from rich.rule import Rule

import config
from llm import create_client, parse_json_response, stream_chat_with_display

# ── System Prompts ────────────────────────────────────────────────────────────

_PM_ARCHITECT_REVIEW = """\
You are the autonomous Project Manager for the Habr Agentic Pipeline dev team.

Your job: review the Architect agent's skeleton files and proposed development subtasks.

Evaluation criteria:

1. SKELETON QUALITY
   - Complete type signatures for all functions/methods
   - Clear docstrings explaining purpose
   - Proper TODO comments for implementation details
   - Correct imports and class hierarchies

2. SUBTASK BREAKDOWN
   - Each subtask is focused and implementable independently
   - Clear description with enough context for a developer agent
   - No overlap between subtasks
   - Reasonable scope (not too large, not too granular)

3. ALIGNMENT WITH SPEC
   - Skeleton files match the original task requirements
   - No missing files that the spec requires
   - No extraneous files

Decision rules:
- APPROVE if skeletons are complete and subtasks are well-defined
- REJECT if missing critical files, subtasks are too vague, or approach is wrong

Respond with ONLY a JSON object:
{
  "approved": true | false,
  "feedback": "specific issues or empty string if approved",
  "subtask_modifications": []
}

subtask_modifications is an optional list of dicts like:
  {"index": 0, "title": "...", "description": "..."}
to suggest changes to specific subtasks (by their 0-based index).
"""

_PM_DEVELOPER_REVIEW = """\
You are the autonomous Project Manager for the Habr Agentic Pipeline dev team.

Your job: review completed development work.

The Reviewer agent already checked code correctness, conventions, and completeness.
Your role is a strategic review from the project perspective.

Evaluation criteria:

1. BUSINESS LOGIC — implementation actually solves the task requirement
2. INTEGRATION — new code integrates cleanly with existing codebase
3. CORRECTNESS — no obvious logical errors or missed edge cases
4. COMPLETENESS — all TODO items from skeletons are implemented

Decision rules:
- APPROVE if implementation is production-ready
- REJECT if fixable issues found (provide specific feedback)

Respond with ONLY a JSON object:
{
  "approved": true | false,
  "feedback": "specific revisions needed or empty if approved"
}
"""

_PM_TESTING_REVIEW = """\
You are the autonomous Project Manager for the Habr Agentic Pipeline dev team.

Your job: final review before marking a task as done.

You receive implementation files, test files, and tox/CI output.

Decision rules:
- APPROVE (→ done) if all tests pass and implementation is complete
- REJECT (→ back to develop) if tests fail or there are fixable issues

Respond with ONLY a JSON object:
{
  "approved": true | false,
  "feedback": "reason for decision"
}
"""


class PMAgent:
    """Autonomous PM that reviews agent output at each pipeline stage."""

    def __init__(self):
        self.client = create_client("pm")

    # ── Review methods ────────────────────────────────────────────────────────

    def review_architect(
        self,
        task: dict,
        files: list[dict],
        subtasks: list[dict],
        summary: str,
    ) -> dict:
        """Review architect skeleton files and proposed subtasks."""
        prompt = self._build_architect_prompt(task, files, subtasks, summary)
        return self._run_review("Architect Review", _PM_ARCHITECT_REVIEW, prompt)

    def review_developer(
        self,
        task: dict,
        files: list[dict],
        summary: str,
    ) -> dict:
        """Review developer implementation."""
        prompt = self._build_developer_prompt(task, files, summary)
        return self._run_review("Developer Review", _PM_DEVELOPER_REVIEW, prompt)

    def review_testing(
        self,
        task: dict,
        files: list[dict],
        tox_output: str,
        summary: str,
    ) -> dict:
        """Final review of test results before marking done."""
        prompt = self._build_testing_prompt(task, files, tox_output, summary)
        return self._run_review("Testing Review", _PM_TESTING_REVIEW, prompt)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run_review(self, title: str, system_prompt: str, user_prompt: str) -> dict:
        console = config.console
        pm = config.step("pm")
        console.print(Rule(
            f"[bold]PM — {title}[/bold]  ·  {pm['backend']}  ·  {pm['model']}",
            style="blue",
        ))

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
            result = parse_json_response(content)
        except Exception as e:
            console.print(f"[red]  PM review error: {e}[/red]")
            result = {"approved": False, "feedback": f"PM review failed: {e}"}

        _print_decision(title, result)
        return result

    def _build_architect_prompt(
        self, task: dict, files: list[dict], subtasks: list[dict], summary: str,
    ) -> str:
        lines = [
            "TASK SPECIFICATION:",
            f"Title: {task['title']}",
            f"Priority: {task['priority']}",
            "",
            task.get("description", "No description."),
            "",
            "ARCHITECT SUMMARY:",
            summary,
            "",
            f"SKELETON FILES ({len(files)}):",
            "",
        ]
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
            lines.append(f"PROPOSED SUBTASKS ({len(subtasks)}):")
            lines.append("")
            for i, st in enumerate(subtasks):
                lines.append(f"  [{i}] {st['title']}")
                lines.append(f"      {st.get('description', '')}")
                lines.append("")

        return "\n".join(lines)

    def _build_developer_prompt(
        self, task: dict, files: list[dict], summary: str,
    ) -> str:
        lines = [
            "TASK SPECIFICATION:",
            f"Title: {task['title']}",
            f"Priority: {task['priority']}",
            "",
            task.get("description", "No description."),
            "",
            "DEVELOPER SUMMARY:",
            summary,
            "",
            f"IMPLEMENTATION FILES ({len(files)}):",
            "",
        ]
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

    def _build_testing_prompt(
        self, task: dict, files: list[dict], tox_output: str, summary: str,
    ) -> str:
        lines = [
            "TASK SPECIFICATION:",
            f"Title: {task['title']}",
            "",
            task.get("description", "No description."),
            "",
            "CI SUMMARY:",
            summary,
            "",
            "TOX OUTPUT (last 80 lines):",
            "\n".join(tox_output.strip().splitlines()[-80:]) if tox_output else "(no output)",
            "",
            f"FILES ({len(files)}):",
            "",
        ]
        for f in files:
            lines.append(f"  - {f['path']}  ({len(f['content'])} chars)")
        return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _print_decision(title: str, result: dict) -> None:
    console = config.console
    console.print(Rule())
    if result.get("approved"):
        console.print(Panel(
            "[bold green]✓ APPROVED[/bold green]",
            border_style="green",
            title=f"PM — {title}",
        ))
    else:
        feedback = result.get("feedback", "No feedback provided.")
        console.print(Panel(
            f"[bold red]✗ REJECTED[/bold red]\n\n{feedback}",
            border_style="red",
            title=f"PM — {title}",
        ))
