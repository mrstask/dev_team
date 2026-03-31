"""Reviewer agent — validates generated code against the task spec."""
from rich.panel import Panel
from rich.rule import Rule

import config
from core import create_client, parse_json_response, stream_chat_with_display
from dtypes import ReviewResult
from prompts import REVIEWER_SYSTEM_PROMPT


class ReviewerAgent:
    def __init__(self):
        self.client = create_client("reviewer")

    def run(self, task: dict, files: list[dict], agent_summary: str) -> ReviewResult:
        """Review generated files against the task spec."""
        config.print_agent_rule("Reviewer", "reviewer")

        prompt = self._build_review_prompt(task, files, agent_summary)

        try:
            final_resp, _ = stream_chat_with_display(
                self.client,
                messages=[
                    {"role": "system", "content": REVIEWER_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                timeout=240,
            )
            content = final_resp.get("message", {}).get("content", "")
            raw = parse_json_response(content)
            result = ReviewResult(**raw)
        except Exception as e:
            config.console.print(f"[red]  Reviewer error: {e}[/red]")
            result = ReviewResult(
                approved=False,
                issues=[f"Reviewer failed with exception: {e}"],
                overall_comment="Review could not complete.",
            )

        _print_review(result)
        return result

    @staticmethod
    def _build_review_prompt(task: dict, files: list[dict], summary: str) -> str:
        lines = [
            "TASK SPECIFICATION:",
            f"Title: {task['title']}",
            "",
            task.get("description", "No description."),
            "",
            "AGENT IMPLEMENTATION SUMMARY:",
            summary,
            "",
            f"GENERATED FILES ({len(files)} total):",
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _print_review(result: ReviewResult) -> None:
    console = config.console
    console.print(Rule())
    if result.approved:
        console.print(Panel(
            f"[bold green]✓ APPROVED[/bold green]\n\n{result.overall_comment}",
            border_style="green",
            title="Code Review",
        ))
    else:
        issues_text = "\n".join(f"  • {i}" for i in result.issues)
        console.print(Panel(
            f"[bold red]✗ REJECTED[/bold red]\n\n"
            f"[bold]Issues:[/bold]\n{issues_text}\n\n"
            f"[bold]Comment:[/bold] {result.overall_comment}",
            border_style="red",
            title="Code Review",
        ))
