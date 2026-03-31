"""CIAgent — writes approved files, runs tox, commits on green."""
import subprocess
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

import config
from ollama_client import OllamaClient

console = Console()

_COMMIT_SYSTEM = """/no_think
You are a git commit message author.
Write a single conventional commit message for the changes described.
Format: <type>(<scope>): <short description>

Types: feat, fix, test, refactor, chore
Scope: the main module or area changed (e.g. models, schemas, pipeline, tests)
Short description: imperative, ≤72 chars total, no period at end.

Respond with ONLY the commit message string, nothing else.
"""


class CIAgent:
    def __init__(self):
        ci = config.step("ci")
        self.model  = ci["model"]
        self.client = OllamaClient(config.OLLAMA_URL, self.model)

    def run(self, task: dict, files: list[dict], summary: str) -> dict:
        """
        1. Write files to disk
        2. Run tox from project root
        3. If green  → generate commit message via LLM, git commit, return {"status": "committed", "sha": ...}
        4. If red    → return {"status": "failed", "output": last N lines}
        """
        ci = config.step("ci")
        console.print(Rule(
            f"[bold]CI Agent[/bold]  ·  {ci['backend']}  ·  {ci['model']}  ·  {len(files)} file(s)",
            style="green",
        ))

        # ── 1. Write files ────────────────────────────────────────────────────
        written: list[Path] = []
        for f in files:
            p = config.ROOT / f["path"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f["content"], encoding="utf-8")
            written.append(p)
            console.print(f"  [green]wrote[/green] {f['path']}")

        # ── 2. Run tox ────────────────────────────────────────────────────────
        console.print("\n[dim]  Running tox ...[/dim]")
        tox_output = ""
        try:
            process = subprocess.Popen(
                ["tox"],
                cwd=str(config.ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Merge stderr into stdout
                text=True,
                bufsize=1,
            )
            
            with Live("", console=console, refresh_per_second=4, transient=True) as live:
                while True:
                    line = process.stdout.readline()
                    if not line and process.poll() is not None:
                        break
                    if line:
                        tox_output += line
                        live.update(Text(f"  {line.strip()}", style="dimitalic"))
            
            process.wait(timeout=300)
            returncode = process.returncode
        except Exception as e:
            console.print(f"[red]  tox execution failed: {e}[/red]")
            return {"status": "failed", "output": str(e)}

        last_lines = "\n".join(tox_output.splitlines()[-40:])

        if returncode != 0:
            console.print(Panel(last_lines, title="[red]tox FAILED[/red]", border_style="red"))
            # Roll back written files so workspace stays clean
            for p in written:
                try:
                    p.unlink()
                except OSError:
                    pass
            return {"status": "failed", "output": last_lines}

        console.print(Panel(last_lines, title="[green]tox PASSED[/green]", border_style="green"))

        # ── 3. Commit ─────────────────────────────────────────────────────────
        commit_msg = self._generate_commit_message(task, files, summary)
        console.print(f"[dim]  Commit message: {commit_msg}[/dim]")

        rel_paths = [str(p.relative_to(config.ROOT)) for p in written]
        subprocess.run(["git", "add", "--"] + rel_paths, cwd=str(config.ROOT), check=True)

        commit_result = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=str(config.ROOT),
            capture_output=True,
            text=True,
        )
        if commit_result.returncode != 0:
            console.print(f"[red]  git commit failed: {commit_result.stderr}[/red]")
            return {"status": "commit_failed", "output": commit_result.stderr}

        sha = _get_head_sha(config.ROOT)
        console.print(f"[bold green]  ✓ Committed {sha[:8]}: {commit_msg}[/bold green]")
        return {"status": "committed", "sha": sha, "commit_message": commit_msg}

    # ── Helpers ───────────────────────────────────────────────────────────────

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
                    {"role": "system", "content": _COMMIT_SYSTEM},
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
        # Fallback
        return f"feat: {task['title'][:65]}"


def _get_head_sha(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()
