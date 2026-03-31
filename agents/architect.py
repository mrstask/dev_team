"""ClaudeAgent — architect role powered by Claude Code CLI via Agent SDK.

Uses the local `claude` CLI (your Claude Code account) — no API key needed.
The agent writes skeleton files to dev_team/_staging/ so nothing touches the
real project paths until PM approval.

In autonomous mode, the architect also proposes development subtasks in its
summary output, which the PM agent reviews before creating them.
"""
import re
import shutil
from pathlib import Path

from rich.panel import Panel
from rich.rule import Rule

import config
from clients.claude_client import ClaudeClient
from core import ROLES
from prompts import STAGING_INSTRUCTION

# Staging dir — agent writes here; PM reviews; CI agent writes to real paths
STAGING_DIR: Path = config.ROOT / "dev_team" / "_staging"


class ClaudeAgent:
    """Architect agent powered by Claude Code CLI (no API key required)."""

    def __init__(self, role: str = "architect"):
        self.role = role
        self.role_def = ROLES[role]
        # Strip /no_think — Qwen3-only directive
        system = self.role_def["system_prompt"]
        self.system_prompt = system.lstrip("/no_think").strip() + STAGING_INSTRUCTION
        self.client = ClaudeClient(console=config.console)

    def run(
        self,
        task: dict,
        feedback: str = "",
        skeleton_files: list[dict] | None = None,
    ) -> dict | None:
        arch = config.step("architect")
        config.console.print(Rule(
            f"[bold]{self.role_def['name']}[/bold]  ·  {arch['backend']}  ·  {arch['model']}",
            style="magenta",
        ))

        # Clear and recreate staging dir
        if STAGING_DIR.exists():
            shutil.rmtree(STAGING_DIR)
        STAGING_DIR.mkdir(parents=True)

        prompt = self._build_prompt(task, feedback, skeleton_files)

        try:
            response = self.client.run(
                prompt,
                system_prompt=self.system_prompt,
                model=arch["model"],
                cwd=str(config.ROOT),
                allowed_tools=["Read", "Glob", "Grep", "Write"],
                max_turns=40,
            )
        except Exception as exc:
            config.console.print(f"[red]Claude agent error: {exc}[/red]")
            shutil.rmtree(STAGING_DIR, ignore_errors=True)
            return None

        # Collect everything the agent wrote to staging
        files: list[dict] = []
        for p in sorted(STAGING_DIR.rglob("*")):
            if p.is_file():
                rel = p.relative_to(STAGING_DIR)
                try:
                    content = p.read_text(encoding="utf-8")
                except Exception:
                    content = p.read_bytes().decode("utf-8", errors="replace")
                files.append({"path": str(rel), "content": content})

        # Clean up staging
        shutil.rmtree(STAGING_DIR, ignore_errors=True)

        if not files:
            config.console.print("[red]Architect wrote no files to staging.[/red]")
            return None

        summary = response.summary or f"Produced {len(files)} skeleton file(s)."
        config.console.print(Panel(f"[bold]Architect summary:[/bold]\n{summary}", border_style="magenta"))
        config.console.print(f"[bold]{len(files)} skeleton file(s) staged.[/bold]")
        for f in files:
            config.console.print(f"  [cyan]{f['path']}[/cyan]  ({len(f['content'])} chars)")

        subtasks = self._extract_subtasks(summary, task, files)
        if subtasks:
            config.console.print(f"[bold]{len(subtasks)} subtask(s) proposed.[/bold]")
            for i, st in enumerate(subtasks):
                config.console.print(f"  [{i}] [cyan]{st['title']}[/cyan]")

        return {
            "status": "pending_review",
            "files": files,
            "summary": summary,
            "subtasks": subtasks,
        }

    def _extract_subtasks(
        self,
        summary: str,
        task: dict,
        files: list[dict],
    ) -> list[dict]:
        """Parse subtask proposals from the architect's summary.

        Expected format in the summary:
            SUBTASKS:
            1. [Title here] Description of focused implementation unit
            2. [Title here] Description of focused implementation unit

        Falls back to a single catch-all subtask if none are found.
        """
        subtasks: list[dict] = []
        in_section = False

        for line in summary.splitlines():
            if re.match(r"^\s*SUBTASKS\s*:?\s*$", line, re.IGNORECASE):
                in_section = True
                continue
            if in_section:
                m = re.match(r"^\s*\d+\.\s*\[([^\]]+)\]\s*(.+)$", line.strip())
                if m:
                    subtasks.append({
                        "title": m.group(1).strip(),
                        "description": m.group(2).strip(),
                        "priority": task["priority"],
                        "labels": ["developer"],
                    })
                elif line.strip() and not re.match(r"^\s*\d+\.", line):
                    break  # end of subtask section

        if not subtasks:
            # Default: single subtask covering all skeleton files
            file_list = "\n".join(f"- {f['path']}" for f in files[:30])
            subtasks = [{
                "title": f"Implement: {task['title'][:60]}",
                "description": (
                    "Implement all TODOs in the following skeleton files:\n\n"
                    f"{file_list}"
                ),
                "priority": task["priority"],
                "labels": ["developer"],
            }]

        return subtasks

    def _build_prompt(
        self,
        task: dict,
        feedback: str,
        skeleton_files: list[dict] | None,
    ) -> str:
        labels = ", ".join(task.get("labels", []))
        prompt = (
            f"Task: {task['title']}\n"
            f"Priority: {task['priority']}\n"
            f"Labels: {labels}\n\n"
            f"Description:\n{task.get('description', 'No description.')}\n\n"
            "Instructions:\n"
            "1. Read reference files as needed (use their real paths).\n"
            "2. Produce skeleton files with typed signatures, docstrings, and TODO comments.\n"
            "3. Write every skeleton file to dev_team/_staging/<real-path>.\n"
            "4. In your final summary, propose development subtasks.\n\n"
            "After writing all skeleton files, end your summary with a SUBTASKS section:\n\n"
            "SUBTASKS:\n"
            "1. [Short title] Description of a focused implementation unit\n"
            "2. [Short title] Description of another unit\n\n"
            "Each subtask should be independently implementable by a Developer agent.\n"
            "Split by module, layer, or feature — avoid subtasks that are too large (>300 LOC)\n"
            "or too small (single function). Include enough context in each description\n"
            "for the developer to work without seeing the full task spec.\n"
        )
        if skeleton_files:
            prompt += f"\nSkeleton files to implement ({len(skeleton_files)} files):\n"
            for f in skeleton_files:
                prompt += f"\n=== {f['path']} ===\n{f['content']}\n"
            prompt += "\nImplement every TODO. Write the complete files to dev_team/_staging/.\n"
        if feedback:
            prompt += f"\nPM feedback:\n{feedback}\n"
        return prompt
