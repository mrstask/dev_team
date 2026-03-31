"""Claude Code SDK client — wraps the local claude CLI via the Agent SDK."""
from dataclasses import dataclass, field

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


@dataclass
class ClaudeResponse:
    """Structured result from a Claude Code SDK query."""

    summary: str = ""
    tool_calls: list[dict] = field(default_factory=list)


class ClaudeClient:
    """Thin wrapper around the Claude Code SDK ``query()`` stream."""

    def __init__(self, console: Console | None = None):
        self.console = console or Console()

    def run(
        self,
        prompt: str,
        *,
        system_prompt: str,
        model: str,
        cwd: str,
        allowed_tools: list[str] | None = None,
        max_turns: int = 40,
    ) -> ClaudeResponse:
        """Synchronous entry point — runs the async query under ``anyio``."""
        return anyio.run(
            self._run_async,
            prompt,
            system_prompt,
            model,
            cwd,
            allowed_tools or ["Read", "Glob", "Grep", "Write"],
            max_turns,
        )

    async def _run_async(
        self,
        prompt: str,
        system_prompt: str,
        model: str,
        cwd: str,
        allowed_tools: list[str],
        max_turns: int,
    ) -> ClaudeResponse:
        options = ClaudeAgentOptions(
            cwd=cwd,
            allowed_tools=allowed_tools,
            permission_mode="bypassPermissions",
            system_prompt=system_prompt,
            model=model,
            max_turns=max_turns,
        )

        summary_parts: list[str] = []
        tool_calls: list[dict] = []

        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        for line in block.text.strip().splitlines():
                            if line.strip():
                                self.console.print(f"  [dim]{line}[/dim]")
                    elif isinstance(block, ToolUseBlock):
                        name = block.name
                        inp = block.input or {}
                        arg = next(iter(inp.values()), "") if inp else ""
                        arg_str = repr(arg)[:60] if isinstance(arg, str) else "..."
                        self.console.print(f"  [green]⚙[/green] [bold]{name}[/bold]({arg_str})")
                        tool_calls.append({"name": name, "input": inp})
            elif isinstance(message, SystemMessage):
                if getattr(message, "subtype", None) == "init":
                    sid = getattr(message, "session_id", None) or getattr(
                        getattr(message, "data", None), "get", lambda k, d=None: d
                    )("session_id")
                    if sid:
                        self.console.print(f"  [dim]session {sid}[/dim]")
            elif isinstance(message, ResultMessage):
                summary_parts.append(message.result or "")

        return ClaudeResponse(
            summary="\n".join(summary_parts).strip(),
            tool_calls=tool_calls,
        )
