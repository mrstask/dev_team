"""Reviewer agent — validates generated code against the task spec."""
from rich.panel import Panel
from rich.rule import Rule

import config
from core import create_client, parse_json_response, stream_chat_with_display
from prompts import REVIEWER_SYSTEM_PROMPT


class ReviewerAgent:
    def __init__(self):
        self.client = create_client("reviewer")

    def review(self, task: dict, files: list[dict], agent_summary: str) -> dict:
        """
        Review generated files against the task spec.
        Returns {"approved": bool, "issues": list[str], "overall_comment": str}
        """
        console = config.console
        prompt = _build_review_prompt(task, files, agent_summary)

        rev = config.step("reviewer")
        console.print(Rule(
            f"[bold]Reviewer[/bold]  ·  {rev['backend']}  ·  {rev['model']}",
            style="yellow",
        ))

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
            result = parse_json_response(content)
        except Exception as e:
            console.print(f"[red]  Reviewer error: {e}[/red]")
            result = {
                "approved": False,
                "issues": [f"Reviewer failed with exception: {e}"],
                "overall_comment": "Review could not complete.",
            }

        _print_review(result)
        return result


# ── Helpers ───────────────────────────────────────────────────────────────────

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


def _print_review(result: dict) -> None:
    console = config.console
    console.print(Rule())
    if result.get("approved"):
        console.print(Panel(
            f"[bold green]✓ APPROVED[/bold green]\n\n{result.get('overall_comment', '')}",
            border_style="green",
            title="Code Review",
        ))
    else:
        issues_text = "\n".join(f"  • {i}" for i in result.get("issues", []))
        console.print(Panel(
            f"[bold red]✗ REJECTED[/bold red]\n\n"
            f"[bold]Issues:[/bold]\n{issues_text}\n\n"
            f"[bold]Comment:[/bold] {result.get('overall_comment', '')}",
            border_style="red",
            title="Code Review",
        ))
