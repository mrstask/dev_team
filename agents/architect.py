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

import config
from clients.claude_client import ClaudeClient
from core import ROLES
from dtypes import ArchitectResult, FileContent, SubtaskProposal
from prompts import ARCHITECT_USER_PROMPT, STAGING_INSTRUCTION

# Staging dir — agent writes here; PM reviews; CI agent writes to real paths
STAGING_DIR: Path = config.ROOT / "dev_team" / "_staging"


class ClaudeAgent:
    """Architect agent powered by Claude Code CLI (no API key required)."""

    def __init__(self, role: str = "architect"):
        self.role = role
        self.role_def = ROLES[role]
        self.system_prompt = self.role_def["system_prompt"].strip() + STAGING_INSTRUCTION
        self.client = ClaudeClient(console=config.console)

    def run(self, task: dict, feedback: str = "", skeleton_files: list[dict] | None = None) -> ArchitectResult | None:
        config.print_agent_rule(self.role_def["name"], "architect")

        # Clear and recreate staging dir
        if STAGING_DIR.exists():
            shutil.rmtree(STAGING_DIR)
        STAGING_DIR.mkdir(parents=True)

        prompt = self._build_prompt(task, feedback, skeleton_files)

        try:
            response = self.client.run(
                prompt,
                system_prompt=self.system_prompt,
                model=config.step("architect")["model"],
                cwd=str(config.ROOT),
                allowed_tools=["Read", "Glob", "Grep", "Write"],
                max_turns=40,
            )
        except Exception as exc:
            config.console.print(f"[red]Claude agent error: {exc}[/red]")
            shutil.rmtree(STAGING_DIR, ignore_errors=True)
            return None

        files = self._collect_staging_files()
        shutil.rmtree(STAGING_DIR, ignore_errors=True)

        if not files:
            config.console.print("[red]Architect wrote no files to staging.[/red]")
            return None

        summary = response.summary or f"Produced {len(files)} skeleton file(s)."
        subtasks = self._extract_subtasks(summary, task, files)

        result = ArchitectResult(files=files, summary=summary, subtasks=subtasks)

        config.console.print(Panel(f"[bold]Architect summary:[/bold]\n{summary}", border_style="magenta"))
        config.console.print(f"[bold]{len(files)} skeleton file(s) staged.[/bold]")
        for f in files:
            config.console.print(f"  [cyan]{f.path}[/cyan]  ({len(f.content)} chars)")
        if subtasks:
            config.console.print(f"[bold]{len(subtasks)} subtask(s) proposed.[/bold]")
            for i, st in enumerate(subtasks):
                config.console.print(f"  [{i}] [cyan]{st.title}[/cyan]")

        return result

    @staticmethod
    def _collect_staging_files() -> list[FileContent]:
        files: list[FileContent] = []
        for p in sorted(STAGING_DIR.rglob("*")):
            if p.is_file():
                rel = p.relative_to(STAGING_DIR)
                try:
                    content = p.read_text(encoding="utf-8")
                except Exception:
                    content = p.read_bytes().decode("utf-8", errors="replace")
                files.append(FileContent(path=str(rel), content=content))
        return files

    @staticmethod
    def _extract_subtasks(summary: str, task: dict, files: list[FileContent]) -> list[SubtaskProposal]:
        """Parse subtask proposals from the architect's summary.

        Expected format in the summary:
            SUBTASKS:
            1. [Title here] Description of focused implementation unit
            2. [Title here] Description of focused implementation unit

        Falls back to a single catch-all subtask if none are found.
        """
        subtasks: list[SubtaskProposal] = []
        in_section = False

        for line in summary.splitlines():
            if re.match(r"^\s*SUBTASKS\s*:?\s*$", line, re.IGNORECASE):
                in_section = True
                continue
            if in_section:
                m = re.match(r"^\s*\d+\.\s*\[([^\]]+)\]\s*(.+)$", line.strip())
                if m:
                    subtasks.append(SubtaskProposal(
                        title=m.group(1).strip(),
                        description=m.group(2).strip(),
                        priority=task["priority"],
                    ))
                elif line.strip() and not re.match(r"^\s*\d+\.", line):
                    break  # end of subtask section

        if not subtasks:
            file_list = "\n".join(f"- {f.path}" for f in files[:30])
            subtasks = [SubtaskProposal(
                title=f"Implement: {task['title'][:60]}",
                description=f"Implement all TODOs in the following skeleton files:\n\n{file_list}",
                priority=task["priority"],
            )]

        return subtasks

    @staticmethod
    def _build_prompt(task: dict, feedback: str, skeleton_files: list[dict] | None) -> str:
        prompt = ARCHITECT_USER_PROMPT.format(
            title=task["title"],
            priority=task["priority"],
            labels=", ".join(task.get("labels", [])),
            description=task.get("description", "No description."),
        )
        if skeleton_files:
            prompt += f"\nSkeleton files to implement ({len(skeleton_files)} files):\n"
            for f in skeleton_files:
                prompt += f"\n=== {f['path']} ===\n{f['content']}\n"
            prompt += "\nImplement every TODO. Write the complete files to dev_team/_staging/.\n"
        if feedback:
            prompt += f"\nPM feedback:\n{feedback}\n"
        return prompt
