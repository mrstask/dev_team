"""Shared ReAct loop — tool calling with streaming and text-based fallback.

Used by DevAgent and TestAgent to avoid duplicating the core loop logic.
"""
import json
import re
import time
from collections.abc import Callable

import config
from clients import OllamaClient, OpenRouterClient
from .llm import LLMRateLimitError, LLMStallError, stream_chat_with_display
from .tools import TOOL_SPECS, dispatch


# ── ReAct loop ────────────────────────────────────────────────────────────────

def run_react_loop(
    client: OllamaClient | OpenRouterClient,
    messages: list[dict],
    *,
    max_rounds: int = config.MAX_TOOL_ROUNDS,
    tools: list[dict] | None = None,
    temperature: float = 0.05,
    fallback_client: OllamaClient | OpenRouterClient | None = None,
    on_write_files: Callable[[dict], dict | list | None] | None = None,
    on_loop_complete: Callable[[list[dict]], None] | None = None,
) -> dict | None:
    """
    Generic ReAct loop with tool calling + text-based fallback.

    Args:
        client: LLM client (Ollama or OpenRouter).
        messages: Initial message list (system + user prompt).
        max_rounds: Max iterations before giving up.
        tools: Tool specs for function calling. Defaults to TOOL_SPECS.
        temperature: LLM temperature.
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

        stall_attempt = 0
        while True:
            try:
                resp, content = stream_chat_with_display(
                    client, messages,
                    tools=tools, temperature=temperature,
                    fallback_client=fallback_client,
                )
                break
            except LLMRateLimitError as e:
                # Both primary and fallback are rate-limited — wait then retry
                console.print(f"[yellow]  Both models rate-limited — waiting {e.retry_after}s…[/yellow]")
                for remaining in range(e.retry_after, 0, -10):
                    console.print(f"  [dim]retrying in {remaining}s…[/dim]", end="\r")
                    time.sleep(min(10, remaining))
                console.print()
            except LLMStallError as e:
                stall_attempt += 1
                if stall_attempt >= config.LLM_STALL_MAX_RETRIES:
                    console.print(f"[red]  LLM stalled {config.LLM_STALL_MAX_RETRIES} times — giving up.[/red]")
                    if on_loop_complete:
                        on_loop_complete(messages)
                    return None
                console.print(f"[yellow]  LLM stalled ({e}). Retrying {stall_attempt}/{config.LLM_STALL_MAX_RETRIES}…[/yellow]")
            except Exception as e:
                console.print(f"[red]  LLM error: {e}[/red]")
                if on_loop_complete:
                    on_loop_complete(messages)
                return None

        _print_reasoning(content)

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
            if on_loop_complete:
                on_loop_complete(messages)
            return None

        console.print()
        _echo_tool_calls(messages, content, tool_calls)

        for call in tool_calls:
            done, result = _dispatch_tool_call(call, messages, on_write_files)
            if done:
                if on_loop_complete:
                    on_loop_complete(messages)
                return result

    console.print(f"[red]Max rounds ({max_rounds}) reached without write_files.[/red]")
    if on_loop_complete:
        on_loop_complete(messages)
    return None


# ── ReAct loop helpers ────────────────────────────────────────────────────────

def _print_reasoning(content: str) -> None:
    clean = content.strip()
    if not clean:
        return
    lines = clean.splitlines()
    # Print up to 6 lines; truncate long ones
    for line in lines[:6]:
        if len(line) > 160:
            line = line[:157] + "..."
        config.console.print(f"  [dim]{line}[/dim]")
    if len(lines) > 6:
        config.console.print(f"  [dim]… ({len(lines) - 6} more lines)[/dim]")


def _echo_tool_calls(messages: list[dict], content: str, tool_calls: list[dict]) -> None:
    """Append assistant message with tool calls serialised to JSON strings (OpenAI protocol)."""
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
    messages.append({"role": "assistant", "content": content, "tool_calls": echoed_calls})


def _dispatch_tool_call(
    call: dict,
    messages: list[dict],
    on_write_files: Callable[[dict], dict | list | None] | None,
) -> tuple[bool, dict | None]:
    """Dispatch one tool call. Returns (done, result) — done=True on write_files completion."""
    fn = call.get("function", {})
    name = fn.get("name", "")
    args = fn.get("arguments", {})
    call_id = call.get("id", "")

    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception as e:
            config.console.print(f"[red]  tool args JSON parse failed for '{name}': {e} (len={len(args)})[/red]")
            args = {}

    arg_preview = ", ".join(f"{k}={repr(v)[:60]}" for k, v in args.items())
    config.console.print(f"  [yellow]⚙[/yellow] [bold]{name}[/bold]({arg_preview})")

    result = dispatch(name, args)

    if name in ("write_files", "finish") and isinstance(result, dict) and result.get("status") == "pending_review":
        written = result.get("written", [])
        n = len(written)
        for p in written:
            config.console.print(f"  [green]wrote[/green] {p}")
        config.console.print(f"  [green]✓[/green] {n} file(s) written to disk, pending PM review")
        return True, on_write_files(result) if on_write_files else result

    if name == "submit_research" and isinstance(result, dict) and result.get("status") == "pending_review":
        config.console.print("  [green]✓[/green] Research findings submitted")
        return True, on_write_files(result) if on_write_files else result

    result_str = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    _print_tool_result(name, result_str)
    tool_msg: dict = {"role": "tool", "content": result_str}
    if call_id:
        tool_msg["tool_call_id"] = call_id
    messages.append(tool_msg)
    return False, None


def _print_tool_result(name: str, result_str: str) -> None:
    """Print a short preview of a tool result."""
    preview = result_str.strip()
    lines = preview.splitlines()
    shown = lines[:4]
    suffix = f"  [dim]… +{len(lines) - 4} lines[/dim]" if len(lines) > 4 else ""
    label = f"  [dim cyan]← {name}:[/dim cyan] "
    config.console.print(label + "[dim]" + "\n".join(shown)[:300] + "[/dim]")
    if suffix:
        config.console.print(suffix)


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
