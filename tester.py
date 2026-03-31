"""TestAgent — generates pytest unit tests for approved implementation files."""
from rich.panel import Panel
from rich.rule import Rule

import config
from llm import create_client
from react_loop import run_react_loop

_SYSTEM_PROMPT = """/no_think
You are a senior Python test engineer for the Habr Agentic Pipeline project.

Your job: write comprehensive pytest unit tests for the provided implementation files.

Testing conventions:
- Use pytest (not unittest)
- Async tests: use `pytest-asyncio` with `@pytest.mark.asyncio`
- SQLAlchemy async: use `AsyncSession` with an in-memory SQLite engine for tests
- Mock external services (OpenAI, Ollama, HTTP calls) with `unittest.mock.AsyncMock` / `MagicMock`
- Test file location: `backend/tests/` mirroring the module structure
  e.g. backend/app/models/article.py → backend/tests/test_models_article.py
- Each test file must start with the module-level fixtures if needed

What to test for each module type:
  Models     — table creation, field types, defaults, relationships, association tables
  Schemas    — valid input parsing, missing required fields, field aliases
  Enums      — all enum values present with correct int/str values
  Config     — settings load from env, defaults are correct
  Repositories — CRUD operations with an in-memory DB session
  Services   — business logic with mocked dependencies
  Routes     — FastAPI TestClient with mocked services

Keep tests focused and fast. No real network calls, no real DB files.
Test one thing per test function. Use descriptive names: `test_article_status_enum_values`.

Output format: call write_files with all test files and a summary.
"""


class TestAgent:
    def __init__(self):
        self.client = create_client("tester")

    def generate_tests(self, task: dict, impl_files: list[dict]) -> list[dict] | None:
        """Generate pytest tests for the given implementation files."""
        console = config.console

        py_files = [f for f in impl_files if f["path"].endswith(".py") and f["path"].startswith("backend/")]
        if not py_files:
            console.print("[dim]  No Python files to test — skipping test generation.[/dim]")
            return []

        tst = config.step("tester")
        console.print(Rule(
            f"[bold]Test Agent[/bold]  ·  {tst['backend']}  ·  {tst['model']}",
            style="yellow",
        ))

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_test_prompt(task, py_files)},
        ]

        def _on_write(result: dict) -> list[dict] | None:
            files = result.get("files", [])
            summary = result.get("summary", "")
            console.print(f"  [green]✓[/green] {len(files)} test file(s) generated")
            _print_test_summary(files, summary)
            return files

        return run_react_loop(
            self.client, messages,
            max_rounds=8,
            on_write_files=_on_write,
        )


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
