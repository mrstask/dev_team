"""ClaudeAgent — architect role powered by Claude Code CLI via Agent SDK.

Uses the local `claude` CLI (your Claude Code account) — no API key needed.
The agent writes skeleton files to dev_team/_staging/ so nothing touches the
real project paths until PM approval.
"""
import shutil
from pathlib import Path

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    query,
)
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule

import config
from roles import ROLES

console = Console()

# Staging dir — agent writes here; PM reviews; CI agent writes to real paths
STAGING_DIR: Path = config.ROOT / "dev_team" / "_staging"

# System prompt suffix that redirects writes to staging
_STAGING_INSTRUCTION = f"""
CRITICAL — FILE WRITING RULES:
- You may READ files from anywhere in the project using their normal paths.
- You must WRITE all output files into the staging directory: dev_team/_staging/
- Preserve the full relative path inside staging.
  Example: to produce backend/app/models/article.py
           write to:  dev_team/_staging/backend/app/models/article.py
- Do NOT write to any real project path — staging only.
- Write every file completely (no truncation, no placeholders inside the file).
"""


class ClaudeAgent:
    """Architect agent powered by Claude Code CLI (no API key required)."""

    def __init__(self, role: str = "architect"):
        self.role = role
        self.role_def = ROLES[role]
        # Strip /no_think — Qwen3-only directive
        system = self.role_def["system_prompt"]
        self.system_prompt = system.lstrip("/no_think").strip() + _STAGING_INSTRUCTION

    def run(
        self,
        task: dict,
        feedback: str = "",
        skeleton_files: list[dict] | None = None,
    ) -> dict | None:
        arch = config.step("architect")
        console.print(Rule(
            f"[bold]{self.role_def['name']}[/bold]  ·  {arch['backend']}  ·  {arch['model']}",
            style="magenta",
        ))
        return anyio.run(self._run_async, task, feedback, skeleton_files)

    async def _run_async(
        self,
        task: dict,
        feedback: str,
        skeleton_files: list[dict] | None,
    ) -> dict | None:
        # Clear and recreate staging dir
        if STAGING_DIR.exists():
            shutil.rmtree(STAGING_DIR)
        STAGING_DIR.mkdir(parents=True)

        prompt = self._build_prompt(task, feedback, skeleton_files)
        summary_parts: list[str] = []

        options = ClaudeAgentOptions(
            cwd=str(config.ROOT),
            allowed_tools=["Read", "Glob", "Grep", "Write"],
            permission_mode="bypassPermissions",
            system_prompt=self.system_prompt,
            model=config.step("architect")["model"],
            max_turns=40,
        )

        try:
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            # Stream Claude's thinking text line by line
                            for line in block.text.strip().splitlines():
                                if line.strip():
                                    console.print(f"  [dim]{line}[/dim]")
                        elif isinstance(block, ToolUseBlock):
                            name = block.name
                            inp  = block.input or {}
                            arg  = next(iter(inp.values()), "") if inp else ""
                            arg_str = repr(arg)[:60] if isinstance(arg, str) else "..."
                            console.print(f"  [green]⚙[/green] [bold]{name}[/bold]({arg_str})")
                elif isinstance(message, SystemMessage):
                    if getattr(message, "subtype", None) == "init":
                        sid = getattr(message, "session_id", None) or getattr(getattr(message, "data", None), "get", lambda k, d=None: d)("session_id")
                        if sid:
                            console.print(f"  [dim]session {sid}[/dim]")
                elif isinstance(message, ResultMessage):
                    summary_parts.append(message.result or "")
        except Exception as exc:
            console.print(f"[red]Claude agent error: {exc}[/red]")
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
            console.print("[red]Architect wrote no files to staging.[/red]")
            return None

        summary = "\n".join(summary_parts).strip() or f"Produced {len(files)} skeleton file(s)."
        console.print(Panel(f"[bold]Architect summary:[/bold]\n{summary}", border_style="magenta"))
        console.print(f"[bold]{len(files)} skeleton file(s) staged.[/bold]")
        for f in files:
            console.print(f"  [cyan]{f['path']}[/cyan]  ({len(f['content'])} chars)")

        return {"status": "pending_review", "files": files, "summary": summary}

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
        )
        if skeleton_files:
            prompt += f"\nSkeleton files to implement ({len(skeleton_files)} files):\n"
            for f in skeleton_files:
                prompt += f"\n=== {f['path']} ===\n{f['content']}\n"
            prompt += "\nImplement every TODO. Write the complete files to dev_team/_staging/.\n"
        if feedback:
            prompt += f"\nPM feedback:\n{feedback}\n"
        return prompt
