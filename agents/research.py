"""ResearchAgent — LLM-agnostic read-only codebase explorer.

Runs a ReAct loop with read-only tools (read_file, list_files, search_code)
and terminates when the agent calls submit_research with structured findings.
The result is a compact dict used to pre-populate the Architect's context.

If the agent fails to call submit_research (weak model, no tool call), we
auto-build a research artifact from the files it read during the loop.
"""
import json
import re

import config
from core import RESEARCH_TOOL_SPECS, create_client, create_fallback_client, run_react_loop
from prompts.research import RESEARCH_SYSTEM_PROMPT, RESEARCH_USER_PROMPT


class ResearchAgent:
    def __init__(self):
        self.client = create_client("researcher")
        self.fallback_client = create_fallback_client("researcher")

    def run(self, task: dict) -> dict | None:
        """Explore the codebase for the given task. Returns a research dict or None."""
        config.print_agent_rule("Research Agent", "researcher")

        messages = [
            {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
            {"role": "user", "content": RESEARCH_USER_PROMPT.format(
                title=task["title"],
                description=task.get("description", "No description."),
            )},
        ]

        raw = run_react_loop(
            self.client,
            messages,
            tools=RESEARCH_TOOL_SPECS,
            fallback_client=self.fallback_client,
            on_write_files=lambda r: r,
        )

        if raw:
            findings_str = raw.get("summary", "")
            if findings_str:
                try:
                    result = json.loads(findings_str)
                except (json.JSONDecodeError, ValueError):
                    result = {"summary": findings_str}
                config.console.print(
                    f"  [dim]Research: {len(result.get('relevant_files', []))} relevant file(s), "
                    f"{len(result.get('warnings', []))} warning(s)[/dim]"
                )
                return result

        # Agent didn't call submit_research — auto-build from conversation
        result = _extract_research_from_messages(messages)
        if result and result.get("relevant_files"):
            config.console.print(
                f"  [yellow]Research agent didn't submit — auto-extracted "
                f"{len(result['relevant_files'])} file(s) from conversation.[/yellow]"
            )
            return result

        return None


def _extract_research_from_messages(messages: list[dict]) -> dict:
    """Build a minimal research artifact from tool calls in the conversation.

    Extracts file paths from read_file/list_files results and any assistant
    commentary about patterns or warnings.
    """
    relevant_files: list[str] = []
    assistant_text: list[str] = []

    for msg in messages:
        role = msg.get("role", "")

        # Collect file paths from tool calls
        if role == "assistant":
            text = msg.get("content", "")
            if text:
                assistant_text.append(text)
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except (json.JSONDecodeError, ValueError):
                        args = {}
                if name == "read_file" and args.get("path"):
                    relevant_files.append(args["path"])

        # Extract file paths from list_files results
        if role == "tool":
            content = msg.get("content", "")
            if content and not content.startswith("ERROR") and not content.startswith("No "):
                for line in content.splitlines():
                    line = line.strip()
                    if line and re.match(r"^[\w./_-]+\.\w+$", line):
                        relevant_files.append(line)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_files: list[str] = []
    for f in relevant_files:
        if f not in seen:
            seen.add(f)
            unique_files.append(f)

    summary = " ".join(assistant_text[-3:])[:500] if assistant_text else ""

    return {
        "relevant_files": unique_files[:20],
        "patterns": [],
        "data_flow": "",
        "warnings": [],
        "summary": summary or f"Auto-extracted from {len(unique_files)} files read during research.",
    }
