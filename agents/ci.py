"""CIAgent — writes approved files, runs tox, commits on green."""
import subprocess
from pathlib import Path

from rich.live import Live
from rich.panel import Panel
from rich.text import Text

import config
from core import create_client
from dtypes import CIResult
from prompts import COMMIT_SYSTEM_PROMPT


class CIAgent:
    def __init__(self):
        self.client = create_client("ci")

    def run(self, task: dict, files: list[dict], summary: str) -> CIResult:
        """
        1. Write files to disk
        2. Run tox from project root
        3. If green  → generate commit message via LLM, git commit
        4. If red    → return failed result with tox output
        """
        config.print_agent_rule("CI Agent", "ci", extra=f"{len(files)} file(s)")

        # ── 1. Write files ────────────────────────────────────────────────────
        written: list[Path] = []
        for f in files:
            p = config.ROOT / f["path"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f["content"], encoding="utf-8")
            written.append(p)
            config.console.print(f"  [green]wrote[/green] {f['path']}")

        # ── 2. Run tox ────────────────────────────────────────────────────────
        returncode, tox_output = self._run_tox()
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

        # ── 3. Commit ─────────────────────────────────────────────────────────
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

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
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
                        live.update(Text(f"  {line.strip()}", style="dimitalic"))

            process.wait(timeout=300)
            return process.returncode, tox_output
        except Exception as e:
            return 1, str(e)

    def _generate_commit_message(self, task: dict, files: list[dict], summary: str) -> str:
        paths = ", ".join(f["path"] for f in files[:8])
        if len(files) > 8:
            paths += f" (+{len(files) - 8} more)"

        prompt = (
            f"Task: {task['title']}\n"
            f"Summary: {summary}\n"
            f"Files changed: {paths}\n"
            "Write the commit message."
        )
        try:
            resp = self.client.chat(
                messages=[
                    {"role": "system", "content": COMMIT_SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
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


def _get_head_sha(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()
