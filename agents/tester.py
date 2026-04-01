"""TestAgent — generates pytest unit tests and runs CI for approved implementation files."""
import subprocess
from pathlib import Path

from rich.live import Live
from rich.panel import Panel
from rich.text import Text

import config
from core import create_client, run_react_loop
from dtypes import CIResult, FileContent, TestResult
from prompts import TESTER_AGENT_SYSTEM_PROMPT, TESTER_CI_SYSTEM_PROMPT, TESTER_USER_PROMPT_FOOTER, TESTER_USER_PROMPT_HEADER


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


    def run_ci(self, task: dict, files: list[dict], summary: str) -> CIResult:
        """Write files, run tox, commit on green (tester:ci role)."""
        config.print_agent_rule("Tester — CI", "tester", extra=f"{len(files)} file(s)")

        written: list[Path] = []
        for f in files:
            p = config.ROOT / f["path"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f["content"], encoding="utf-8")
            written.append(p)
            config.console.print(f"  [green]wrote[/green] {f['path']}")

        returncode, tox_output = _run_tox()
        last_lines = "\n".join(tox_output.splitlines()[-40:])

        if returncode != 0:
            config.console.print(Panel(last_lines, title="[red]tox FAILED[/red]", border_style="red"))
            for p in written:
                try:
                    p.unlink()
                except OSError:
                    pass
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

def _run_tox() -> tuple[int, str]:
    tox_output = ""
    config.console.print("\n[dim]  Running tox ...[/dim]")
    try:
        process = subprocess.Popen(
            ["tox"],
            cwd=str(config.ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        with Live("", console=config.console, refresh_per_second=4, transient=True) as live:
            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                if line:
                    tox_output += line
                    live.update(Text(f"  {line.strip()}", style="dim italic"))
        process.wait(timeout=300)
        return process.returncode, tox_output
    except Exception as e:
        return 1, str(e)


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
