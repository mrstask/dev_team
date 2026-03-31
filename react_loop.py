"""Shared ReAct loop — tool calling with streaming and text-based fallback.

Used by DevAgent and TestAgent to avoid duplicating the core loop logic.
"""
import json
import re
from collections.abc import Callable

import config
from llm import stream_chat_with_display
from ollama_client import OllamaClient
from openrouter_client import OpenRouterClient
from tools import TOOL_SPECS, dispatch


# ── ReAct loop ────────────────────────────────────────────────────────────────

def run_react_loop(
    client: OllamaClient | OpenRouterClient,
    messages: list[dict],
    *,
    max_rounds: int = config.MAX_TOOL_ROUNDS,
    tools: list[dict] | None = None,
    temperature: float = 0.05,
    timeout: int = config.OLLAMA_TIMEOUT,
    on_write_files: Callable[[dict], dict | list | None] | None = None,
) -> dict | None:
    """
    Generic ReAct loop with tool calling + text-based fallback.

    Args:
        client: LLM client (Ollama or OpenRouter).
        messages: Initial message list (system + user prompt).
        max_rounds: Max iterations before giving up.
        tools: Tool specs for function calling. Defaults to TOOL_SPECS.
        temperature: LLM temperature.
        timeout: Per-request timeout.
        on_write_files: Optional callback when write_files is called.
            Receives the raw result dict. Return value replaces the default
            return. If None, returns the write_files result directly.

    Returns:
        Agent result dict or None on failure.
    """
    console = config.console
    if tools is None:
        tools = TOOL_SPECS

    for round_num in range(1, max_rounds + 1):
        console.print(f"[dim]  round {round_num}/{max_rounds}[/dim]")

        try:
            resp, content = stream_chat_with_display(
                client, messages,
                tools=tools, temperature=temperature, timeout=timeout,
            )
        except Exception as e:
            console.print(f"[red]  LLM error: {e}[/red]")
            return None

        # Print first line of reasoning
        clean = content.strip()
        if clean:
            first_line = clean.splitlines()[0]
            if len(first_line) > 120:
                first_line = first_line[:117] + "..."
            console.print(f"  {first_line}")

        msg = resp.get("message", {})
        tool_calls = msg.get("tool_calls") or []

        # Fallback: parse tool calls from text
        if not tool_calls and content:
            tool_calls = extract_text_tool_calls(content)
            if tool_calls:
                console.print(f" [dim](text-mode)[/dim]")

        if not tool_calls:
            console.print(" [yellow]no tool call[/yellow]")
            if content:
                from rich.panel import Panel
                console.print(Panel(content[:600], title="Agent text (no files)", border_style="yellow"))
            return None

        console.print()

        # Echo tool calls back (arguments must be JSON string per OpenAI protocol)
        echoed_calls = []
        for tc in tool_calls:
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            echoed_calls.append({
                **tc,
                "function": {
                    **fn,
                    "arguments": json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else args,
                },
            })
        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": echoed_calls,
        })

        # Dispatch each tool call
        for call in tool_calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            args = fn.get("arguments", {})
            call_id = call.get("id", "")

            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}

            arg_preview = ", ".join(f"{k}={repr(v)[:60]}" for k, v in args.items())
            console.print(f"  [yellow]⚙[/yellow] [bold]{name}[/bold]({arg_preview})")

            result = dispatch(name, args)

            # Check for write_files completion
            if (
                name == "write_files"
                and isinstance(result, dict)
                and result.get("status") == "pending_review"
            ):
                n = len(result.get("files", []))
                console.print(f"  [green]✓[/green] {n} file(s) ready")
                if on_write_files:
                    return on_write_files(result)
                return result

            result_str = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            tool_msg: dict = {"role": "tool", "content": result_str}
            if call_id:
                tool_msg["tool_call_id"] = call_id
            messages.append(tool_msg)

    console.print(f"[red]Max rounds ({max_rounds}) reached without write_files.[/red]")
    return None


# ── Text-based tool call extraction ───────────────────────────────────────────

def extract_text_tool_calls(content: str) -> list[dict]:
    """
    Extract tool calls from text when the model doesn't use native tool calling.

    Handles:
      - ```json { "name": "...", "arguments": {...} } ```
      - bare JSON objects with "name" + "arguments" keys
      - {"tool_calls": [...]} wrappers
    """
    calls: list[dict] = []

    # 1. Code blocks
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)

    # 2. Top-level JSON objects
    raw_objects = re.findall(r"\{[^`]*?\}", content, re.DOTALL)
    blocks += raw_objects

    seen: set[str] = set()
    for block in blocks:
        block = block.strip()
        if block in seen:
            continue
        seen.add(block)
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue

        if isinstance(data, dict) and "name" in data and "arguments" in data:
            calls.append({"function": {"name": data["name"], "arguments": data["arguments"]}})
            continue

        if "tool_calls" in data:
            for tc in data["tool_calls"]:
                fn = tc.get("function", {})
                if fn.get("name") and "arguments" in fn:
                    calls.append({"function": fn})

    return calls
