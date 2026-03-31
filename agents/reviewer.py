"""Reviewer agent — validates generated code against the task spec."""
from rich.panel import Panel
from rich.rule import Rule

import config
from core import create_client, parse_json_response, stream_chat_with_display

_SYSTEM_PROMPT = """/no_think
You are a senior code reviewer for the Habr Agentic Pipeline project.

Review the generated files strictly against the task specification.

IMPORTANT — empty file rules:
- A file with content="" (empty string, 0 chars) IS correctly empty. Do NOT flag it.
- __init__.py files in Python packages are SUPPOSED to be empty. Empty = correct.
- Only flag a file as wrong if it is missing entirely OR has incorrect content.

Check ALL of the following:
1. COMPLETENESS  — every file path listed in the spec is present
2. CORRECTNESS   — class names, field names, types, imports match the spec exactly
3. CONVENTIONS   — SQLAlchemy 2.x (Mapped/mapped_column), Pydantic v2 (ConfigDict),
                   async functions where required
4. STRUCTURE     — files are at the correct paths relative to habr-agentic root
5. WRONG FILES   — flag files that should NOT exist (e.g. Python __init__.py inside
                   a TypeScript/React frontend directory)
6. FUNCTIONALITY — for non-empty files: no syntax errors, no broken imports

Minor style differences are NOT blocking.
Missing required files, wrong field names, sync instead of async — ARE blocking.
Empty __init__.py files — are CORRECT, never block on them.

Respond with ONLY a JSON object, no markdown, no other text:
{
  "approved": true | false,
  "issues": ["specific issue 1", "specific issue 2"],
  "overall_comment": "one-sentence summary"
}

approved=true  → issues list should be empty or contain minor non-blocking notes
approved=false → issues must list concrete, fixable problems with file paths
"""


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
                    {"role": "system", "content": _SYSTEM_PROMPT},
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
