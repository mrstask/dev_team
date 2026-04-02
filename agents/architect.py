"""ArchitectAgent — LLM-agnostic architect that reads backend from models.json.

Backend is selected by create_client("architect") based on models.json:
  - "claude-code"  → Claude Code SDK (full agent with file tools)
  - "openrouter"   → OpenRouter via ReAct loop with write_files tool
  - "ollama"       → Local Ollama via ReAct loop with write_files tool

No LLM-specific logic lives in this class. To switch backends, change models.json.
"""
import re
import shutil
from pathlib import Path

from rich.panel import Panel

import config
from clients import ClaudeClient
from core import ROLES, create_client, parse_json_response, run_react_loop, stream_chat_with_display
from dtypes import ArchitectResult, FileContent, ReviewResult, SubtaskProposal
from prompts import ARCHITECT_USER_PROMPT, REVIEWER_USER_PROMPT_HEADER, STAGING_INSTRUCTION

# Staging dir — used by claude-code backend; ReAct backend writes directly
STAGING_DIR: Path = config.ROOT / "dev_team" / "_staging"


class ArchitectAgent:
    """Architect agent — backend determined entirely by models.json."""

    def __init__(self, role: str = "architect:design"):
        self.role = role
        self.role_def = ROLES[role]
        self.client = create_client("architect")

    def run(self, task: dict, feedback: str = "", skeleton_files: list[dict] | None = None) -> ArchitectResult | None:
        config.print_agent_rule(self.role_def["name"], "architect")

        prompt = self._build_prompt(task, feedback, skeleton_files)

        if isinstance(self.client, ClaudeClient):
            return self._run_claude_code(prompt, task)
        return self._run_react(prompt, task)

    # ── Execution strategies ───────────────────────────────────────────────────

    def _run_claude_code(self, prompt: str, task: dict) -> ArchitectResult | None:
        """Run via Claude Code SDK — agent has native file system access."""
        if STAGING_DIR.exists():
            shutil.rmtree(STAGING_DIR)
        STAGING_DIR.mkdir(parents=True)

        system_prompt = self.role_def["system_prompt"].strip() + STAGING_INSTRUCTION
        model = config.step("architect")["model"]

        try:
            response = self.client.run(
                prompt,
                system_prompt=system_prompt,
                model=model,
                cwd=str(config.ROOT),
                allowed_tools=["Read", "Glob", "Grep", "Write"],
                max_turns=40,
            )
        except Exception as exc:
            config.console.print(f"[red]Architect error: {exc}[/red]")
            shutil.rmtree(STAGING_DIR, ignore_errors=True)
            return None

        files = self._collect_staging_files()
        shutil.rmtree(STAGING_DIR, ignore_errors=True)

        if not files:
            config.console.print("[red]Architect wrote no files to staging.[/red]")
            return None

        summary = response.summary or f"Produced {len(files)} skeleton file(s)."
        return self._build_result(files, summary, task)

    def _run_react(self, prompt: str, task: dict) -> ArchitectResult | None:
        """Run via ReAct loop — agent uses write_files tool to submit output."""
        messages = [
            {"role": "system", "content": self.role_def["system_prompt"]},
            {"role": "user", "content": prompt},
        ]

        raw = run_react_loop(
            self.client,
            messages,
            max_rounds=10,
        )

        if not raw:
            config.console.print("[red]Architect produced no output.[/red]")
            return None

        files_raw = raw.get("files", []) if isinstance(raw, dict) else []
        if not files_raw:
            return None

        files = [FileContent(path=_sanitize_path(f["path"]), content=f["content"]) for f in files_raw]
        summary = raw.get("summary", f"Produced {len(files)} skeleton file(s).")
        return self._build_result(files, summary, task)

    # ── Dev review (always uses openrouter/ollama — never claude-code) ─────────

    def run_dev_review(self, task: dict, files: list[dict], agent_summary: str) -> ReviewResult:
        """Code review of developer output against the task spec."""
        role_def = ROLES["architect:dev-review"]
        config.print_agent_rule(role_def["name"], role_def["step"])

        prompt = REVIEWER_USER_PROMPT_HEADER.format(
            title=task["title"],
            description=task.get("description", "No description."),
            summary=agent_summary,
            count=len(files),
        )
        for f in files:
            prompt += f"=== {f['path']} ===\n"
            content = f["content"]
            if len(content) > 4000:
                prompt += content[:4000] + f"\n[... {len(content) - 4000} chars truncated ...]\n"
            else:
                prompt += content + "\n"
            prompt += "\n"

        client = create_client(role_def["step"])
        try:
            final_resp, _ = stream_chat_with_display(
                client,
                messages=[
                    {"role": "system", "content": role_def["system_prompt"]},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                timeout=240,
            )
            content = final_resp.get("message", {}).get("content", "")
            return ReviewResult(**parse_json_response(content))
        except Exception as e:
            config.console.print(f"[red]  Architect dev-review error: {e}[/red]")
            return ReviewResult(
                approved=False,
                issues=[f"Review failed with exception: {e}"],
                overall_comment="Review could not complete.",
            )

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _build_result(self, files: list[FileContent], summary: str, task: dict) -> ArchitectResult:
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
                    break
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
            prompt += "\nImplement every TODO. Write the complete files.\n"
        if feedback:
            prompt += f"\nPM feedback:\n{feedback}\n"
        return prompt


def _sanitize_path(path: str) -> str:
    """Strip accidental project-name prefix from paths."""
    root_name = config.ROOT.name
    prefix = root_name + "/"
    if path.startswith(prefix):
        return path[len(prefix):]
    return path


# Backward-compatible alias
ClaudeAgent = ArchitectAgent
