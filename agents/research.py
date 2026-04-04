"""ResearchAgent — LLM-agnostic read-only codebase explorer.

Runs a ReAct loop with read-only tools (read_file, list_files, search_code)
and terminates when the agent calls submit_research with structured findings.
The result is a compact dict used to pre-populate the Architect's context.
"""
import json

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

        if not raw:
            return None

        findings_str = raw.get("summary", "")
        if not findings_str:
            return None

        try:
            result = json.loads(findings_str)
        except (json.JSONDecodeError, ValueError):
            # Agent returned plain text instead of JSON — wrap it
            result = {"summary": findings_str}

        config.console.print(
            f"  [dim]Research: {len(result.get('relevant_files', []))} relevant file(s), "
            f"{len(result.get('warnings', []))} warning(s)[/dim]"
        )
        return result
