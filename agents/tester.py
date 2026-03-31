"""TestAgent — generates pytest unit tests for approved implementation files."""
from rich.panel import Panel

import config
from core import create_client, run_react_loop
from dtypes import FileContent, TestResult
from prompts import TESTER_AGENT_SYSTEM_PROMPT


class TestAgent:
    def __init__(self):
        self.client = create_client("tester")

    def run(self, task: dict, impl_files: list[dict]) -> TestResult:
        """Generate pytest tests for the given implementation files."""
        console = config.console

        py_files = [f for f in impl_files if f["path"].endswith(".py") and f["path"].startswith("backend/")]
        if not py_files:
            console.print("[dim]  No Python files to test — skipping test generation.[/dim]")
            return TestResult(files=[])

        config.print_agent_rule("Test Agent", "tester")

        messages = [
            {"role": "system", "content": TESTER_AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": _build_test_prompt(task, py_files)},
        ]

        def _on_write(raw: dict) -> list[dict] | None:
            files = raw.get("files", [])
            summary = raw.get("summary", "")
            console.print(f"  [green]✓[/green] {len(files)} test file(s) generated")
            _print_test_summary(files, summary)
            return files

        raw_files = run_react_loop(
            self.client, messages,
            max_rounds=8,
            on_write_files=_on_write,
        )

        if isinstance(raw_files, list):
            return TestResult(files=[FileContent(**f) for f in raw_files])
        if isinstance(raw_files, dict) and raw_files.get("files"):
            return TestResult(files=[FileContent(**f) for f in raw_files["files"]])
        return TestResult(files=[])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_test_prompt(task: dict, py_files: list[dict]) -> str:
    lines = [
        f"Write pytest unit tests for the following implementation.",
        f"Task: {task['title']}",
        "",
        f"Implementation files ({len(py_files)}):",
        "",
    ]
    for f in py_files:
        lines.append(f"=== {f['path']} ===")
        content = f["content"]
        if len(content) > 5000:
            lines.append(content[:5000] + f"\n[...truncated]")
        else:
            lines.append(content)
        lines.append("")
    lines += [
        "Requirements:",
        "- One test file per implementation module",
        "- File paths: backend/tests/test_<module_name>.py",
        "- Use pytest, pytest-asyncio for async, in-memory SQLite for DB tests",
        "- Mock all external I/O (HTTP, file system, env vars where needed)",
        "- Test all enum values, model fields, schema validation, and key logic",
        "- Call write_files with all test files when done",
    ]
    return "\n".join(lines)


def _print_test_summary(files: list[dict], summary: str) -> None:
    config.console.print(Panel(
        "[bold]Tests generated:[/bold]\n" +
        "\n".join(f"  [blue]{f['path']}[/blue]  ({len(f['content'])} chars)" for f in files) +
        (f"\n\n{summary}" if summary else ""),
        border_style="blue",
        title="Test Generation",
    ))
