"""PMAgent — autonomous Project Manager that reviews agent output and makes decisions."""
from rich.panel import Panel
from rich.rule import Rule

import config
from core import create_client, parse_json_response, stream_chat_with_display
from dtypes import ReviewResult
from prompts import PM_ARCHITECT_REVIEW, PM_DEVELOPER_REVIEW, PM_TESTING_REVIEW


class PMAgent:
    """Autonomous PM that reviews agent output at each pipeline stage."""

    def __init__(self):
        self.client = create_client("pm")

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

    @staticmethod
    def _build_developer_prompt(
        task: dict, files: list[dict], summary: str,
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

    @staticmethod
    def _build_testing_prompt(
        task: dict, files: list[dict], tox_output: str, summary: str,
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
