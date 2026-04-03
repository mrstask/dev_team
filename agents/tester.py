"""TestAgent — generates pytest unit tests and runs CI for approved implementation files."""
import shutil
import subprocess
import sys
from pathlib import Path

from rich.panel import Panel

import config
from core import create_client, create_fallback_client, run_react_loop
from dtypes import CIResult, FileContent, TestResult
from prompts import TESTER_AGENT_SYSTEM_PROMPT, TESTER_CI_SYSTEM_PROMPT, TESTER_USER_PROMPT_FOOTER, TESTER_USER_PROMPT_HEADER


class TestAgent:
    def __init__(self):
        self.client = create_client("tester")
        self.fallback_client = create_fallback_client("tester")

    def run(self, task: dict, impl_files: list[dict], on_loop_complete=None) -> TestResult:
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
            fallback_client=self.fallback_client,
            on_write_files=_on_write,
            on_loop_complete=on_loop_complete,
        )

        if isinstance(raw_files, list):
            return TestResult(files=[FileContent(**f) for f in raw_files])
        if isinstance(raw_files, dict) and raw_files.get("files"):
            return TestResult(files=[FileContent(**f) for f in raw_files["files"]])
        return TestResult(files=[])


    def run_ci(self, task: dict, files: list[dict], summary: str) -> CIResult:
        """Write files, run tox, commit on green (tester:ci role)."""
        config.print_agent_rule("Tester — CI", "tester", extra=f"{len(files)} file(s)")
        _ensure_ci_env()

        written: list[Path] = []
        for f in files:
            path = _sanitize_path(f["path"])
            p = config.ROOT / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f["content"], encoding="utf-8")
            written.append(p)
            config.console.print(f"  [green]wrote[/green] {path}")

        returncode, tox_output = _run_tox()
        last_lines = "\n".join(tox_output.splitlines()[-40:])

        if returncode != 0:
            config.console.print(Panel(last_lines, title="[red]tox FAILED[/red]", border_style="red"))
            return CIResult(status="failed", output=last_lines)

        config.console.print(Panel(last_lines, title="[green]tox PASSED[/green]", border_style="green"))

        commit_msg = self._generate_commit_message(task, files, summary)
        config.console.print(f"[dim]  Commit message: {commit_msg}[/dim]")

        rel_paths = [str(p.relative_to(config.ROOT)) for p in written]
        subprocess.run(["git", "add", "--"] + rel_paths, cwd=str(config.ROOT), check=True)

        commit_result = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=str(config.ROOT),
            capture_output=True,
            text=True,
        )
        if commit_result.returncode != 0:
            config.console.print(f"[red]  git commit failed: {commit_result.stderr}[/red]")
            return CIResult(status="commit_failed", output=commit_result.stderr)

        sha = _get_head_sha(config.ROOT)
        config.console.print(f"[bold green]  ✓ Committed {sha[:8]}: {commit_msg}[/bold green]")
        return CIResult(status="committed", sha=sha, commit_message=commit_msg)

    def _generate_commit_message(self, task: dict, files: list[dict], summary: str) -> str:
        paths = ", ".join(f["path"] for f in files[:8])
        if len(files) > 8:
            paths += f" (+{len(files) - 8} more)"

        client = create_client("tester")
        from prompts import COMMIT_USER_PROMPT
        prompt = COMMIT_USER_PROMPT.format(title=task["title"], summary=summary, paths=paths)
        try:
            resp = client.chat(
                messages=[
                    {"role": "system", "content": TESTER_CI_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                timeout=60,
            )
            msg = resp.get("message", {}).get("content", "").strip().splitlines()[0]
            if msg:
                return msg
        except Exception:
            pass
        return f"feat: {task['title'][:65]}"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ensure_ci_env() -> None:
    """Provision the test environment on first use: venv, deps, tests scaffold."""
    console = config.console
    backend = config.BACKEND

    # ── 1. Backend venv ──────────────────────────────────────────────────────
    venv_dir = backend / ".venv"
    venv_python = venv_dir / "bin" / "python"
    if not venv_python.exists():
        console.print("[dim]  CI setup: creating backend/.venv ...[/dim]")
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    pip = venv_dir / "bin" / "pip"

    # ── 2. Install backend deps ───────────────────────────────────────────────
    req = backend / "requirements.txt"
    req_test = backend / "requirements-test.txt"
    if req.exists():
        console.print("[dim]  CI setup: pip install -r requirements.txt ...[/dim]")
        subprocess.run([str(pip), "install", "-q", "-r", str(req)], check=False)
    if req_test.exists():
        console.print("[dim]  CI setup: pip install -r requirements-test.txt ...[/dim]")
        subprocess.run([str(pip), "install", "-q", "-r", str(req_test)], check=False)
    else:
        # Ensure at least pytest + asyncio support is present
        subprocess.run(
            [str(pip), "install", "-q", "pytest", "pytest-asyncio", "aiosqlite"],
            check=False,
        )

    # ── 3. Tests directory scaffold ───────────────────────────────────────────
    tests_dir = backend / "tests"
    tests_dir.mkdir(exist_ok=True)
    init = tests_dir / "__init__.py"
    if not init.exists():
        init.write_text("", encoding="utf-8")
        console.print("[dim]  CI setup: created backend/tests/__init__.py[/dim]")

    conftest = tests_dir / "conftest.py"
    if not conftest.exists():
        conftest.write_text(
            "import pytest\n\n"
            "# Add backend/ to sys.path so tests can import app modules\n"
            "import sys, pathlib\n"
            "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))\n",
            encoding="utf-8",
        )
        console.print("[dim]  CI setup: created backend/tests/conftest.py[/dim]")

    console.print("[dim]  CI setup: done.[/dim]")


def _run_tox() -> tuple[int, str]:
    """Run the test suite. Uses tox if available, falls back to pytest directly."""

    tox_bin = shutil.which("tox")

    def _find_pytest() -> Path | None:
        # Prefer backend venv (set up by _ensure_ci_env), then fall back to .tox envs
        candidates = [
            config.BACKEND / ".venv" / "bin" / "pytest",
            config.BACKEND / "venv" / "bin" / "pytest",
            *sorted((config.ROOT / ".tox").glob("*/bin/pytest")),
        ]
        return next((p for p in candidates if p.exists()), None)

    if tox_bin:
        cmd = [tox_bin]
        cwd = str(config.ROOT)
        label = "tox"
    else:
        found = _find_pytest()
        pytest_bin = str(found) if found else (shutil.which("pytest") or "pytest")
        cmd = [pytest_bin, "tests/", "-v", "--tb=short"]
        cwd = str(config.BACKEND)
        label = f"pytest ({pytest_bin})"

    output = ""
    config.console.print(f"\n[dim]  Running {label} ...[/dim]")
    try:
        process = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in process.stdout:
            output += line
            config.console.print(f"  [dim]{line.rstrip()}[/dim]")
        process.wait(timeout=300)
        return process.returncode, output
    except FileNotFoundError:
        msg = f"ERROR: '{cmd[0]}' not found. Install tox or pytest in the backend venv."
        config.console.print(f"[red]  {msg}[/red]")
        return 1, msg
    except Exception as e:
        config.console.print(f"[red]  CI error: {e}[/red]")
        return 1, str(e)


def _sanitize_path(path: str) -> str:
    """Strip any leading project-name prefix from a file path.

    Agents sometimes prefix paths with the project folder name (e.g.
    'habr-agentic/backend/foo.py'). Strip it so we always write relative
    to config.ROOT.
    """
    root_name = config.ROOT.name  # e.g. "habr-agentic"
    prefix = root_name + "/"
    if path.startswith(prefix):
        stripped = path[len(prefix):]
        config.console.print(
            f"  [yellow]⚠ path prefix '{root_name}/' stripped: {path} → {stripped}[/yellow]"
        )
        return stripped
    return path


def _get_head_sha(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _build_test_prompt(task: dict, py_files: list[dict]) -> str:
    lines = [TESTER_USER_PROMPT_HEADER.format(title=task["title"], count=len(py_files))]
    for f in py_files:
        lines.append(f"=== {f['path']} ===")
        content = f["content"]
        if len(content) > 5000:
            lines.append(content[:5000] + f"\n[...truncated]")
        else:
            lines.append(content)
        lines.append("")
    lines.append(TESTER_USER_PROMPT_FOOTER)
    return "\n".join(lines)


def _print_test_summary(files: list[dict], summary: str) -> None:
    config.console.print(Panel(
        "[bold]Tests generated:[/bold]\n" +
        "\n".join(f"  [blue]{f['path']}[/blue]  ({len(f['content'])} chars)" for f in files) +
        (f"\n\n{summary}" if summary else ""),
        border_style="blue",
        title="Test Generation",
    ))
