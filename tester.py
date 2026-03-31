"""TestAgent — generates pytest unit tests for approved implementation files."""
import json
import re
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

import config
from ollama_client import OllamaClient
from tools import TOOL_SPECS, dispatch
from agent import _extract_text_tool_calls

console = Console()

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
        tst = config.step("tester")
        self.model  = tst["model"]
        self.client = OllamaClient(config.OLLAMA_URL, self.model)

    def generate_tests(self, task: dict, impl_files: list[dict]) -> list[dict] | None:
        """
        Generate pytest tests for the given implementation files.
        Returns list of test file dicts {path, content} or None on failure.
        """
        # Only generate tests for Python backend files
        py_files = [f for f in impl_files if f["path"].endswith(".py") and f["path"].startswith("backend/")]
        if not py_files:
            console.print("[dim]  No Python files to test — skipping test generation.[/dim]")
            return []

        prompt = _build_test_prompt(task, py_files)

        tst = config.step("tester")
        console.print(Rule(
            f"[bold]Test Agent[/bold]  ·  {tst['backend']}  ·  {tst['model']}",
            style="yellow",
        ))

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ]

        for round_num in range(1, 8):
            console.print(f"[dim]  round {round_num}/8 ...[/dim]", end="")

            try:
                accumulated = ""
                final_resp  = {}
                with Live("", console=console, refresh_per_second=10, transient=True) as live:
                    for chunk, final in self.client.stream_chat(
                        messages=messages, tools=TOOL_SPECS, temperature=0.05
                    ):
                        if final is not None:
                            final_resp = final
                            break
                        accumulated += chunk
                        # Show last line of reasoning to indicate progress
                        last_line = accumulated.strip().splitlines()[-1] if accumulated.strip() else ""
                        live.update(Text(f"  {last_line}", style="dimitalic"))

                resp = final_resp
            except Exception as e:
                console.print(f"\n[red]  Test agent error: {e}[/red]")
                return None

            msg        = resp.get("message", {})
            tool_calls = msg.get("tool_calls") or []
            content    = msg.get("content", "")

            if not tool_calls and content:
                tool_calls = _extract_text_tool_calls(content)
                if tool_calls:
                    console.print(f" [dim](text-mode)[/dim]")

            if not tool_calls:
                console.print(" [yellow]no tool call[/yellow]")
                if content:
                    console.print(Panel(content[:400], title="Test agent text", border_style="yellow"))
                return None

            console.print()
            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

            for call in tool_calls:
                fn   = call.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}

                arg_preview = ", ".join(f"{k}={repr(v)[:50]}" for k, v in args.items())
                console.print(f"  [blue]⚙[/blue] [bold]{name}[/bold]({arg_preview})")

                if name == "write_files":
                    files   = args.get("files", [])
                    summary = args.get("summary", "")
                    n = len(files)
                    console.print(f"  [green]✓[/green] {n} test file(s) generated")
                    _print_test_summary(files, summary)
                    return files

                # read_file/list_files allowed for context gathering
                from tools import dispatch
                result     = dispatch(name, args)
                result_str = result if isinstance(result, str) else json.dumps(result)
                messages.append({"role": "tool", "content": result_str})

        console.print("[red]Test agent: max rounds reached.[/red]")
        return None


# ── Helpers ────────────────────────────────────────────────────────────────────

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
    console.print(Panel(
        f"[bold]Tests generated:[/bold]\n" +
        "\n".join(f"  [blue]{f['path']}[/blue]  ({len(f['content'])} chars)" for f in files) +
        (f"\n\n{summary}" if summary else ""),
        border_style="blue",
        title="Test Generation",
    ))
