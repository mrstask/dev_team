"""DevAgent — ReAct loop over Ollama with tool calling + text-based fallback."""
import json
import re
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

import config
from ollama_client import OllamaClient
from openrouter_client import OpenRouterClient
from tools import TOOL_SPECS, dispatch
from roles import ROLES

console = Console()


class DevAgent:
    def __init__(self, role: str, model: str | None = None):
        if role not in ROLES:
            raise ValueError(f"Unknown role: {role}. Available: {list(ROLES.keys())}")
        self.role     = role
        self.role_def = ROLES[role]

        dev = config.step("developer")
        self._backend = model_backend = dev["backend"]
        self.model    = model or dev["model"]

        if model_backend == "openrouter":
            if not config.OPENROUTER_API_KEY:
                raise ValueError("OPENROUTER_API_KEY is not set in .env")
            self.client = OpenRouterClient(config.OPENROUTER_API_KEY, self.model)
        elif model_backend == "ollama":
            self.client = OllamaClient(config.OLLAMA_URL, self.model)
        else:
            raise ValueError(f"Unknown backend '{model_backend}' for developer step in models.json")

    def run(
        self,
        task: dict,
        feedback: str = "",
        skeleton_files: list[dict] | None = None,
        previous_files: list[dict] | None = None,
    ) -> dict | None:
        """
        ReAct loop: read context → write files.

        Supports two tool-call modes:
          • Native  — Ollama returns tool_calls in the message (preferred)
          • Text    — model embeds JSON tool calls in content (fallback for
                      models that don't reliably trigger native tool calling)
        """
        messages = [
            {"role": "system", "content": self.role_def["system_prompt"]},
            {"role": "user",   "content": self._build_prompt(task, feedback, skeleton_files, previous_files)},
        ]

        style = "cyan" if self._backend == "openrouter" else "yellow"
        console.print(Rule(
            f"[bold]{self.role_def['name']}[/bold]  ·  {self._backend}  ·  {self.model}",
            style=style,
        ))

        for round_num in range(1, config.MAX_TOOL_ROUNDS + 1):
            console.print(f"[dim]  round {round_num}/{config.MAX_TOOL_ROUNDS}[/dim]")

            try:
                resp, content = self._stream_round(messages)
            except Exception as e:
                console.print(f"[red]  {self._backend} error: {e}[/red]")
                return None

            msg        = resp.get("message", {})
            tool_calls = msg.get("tool_calls") or []

            # ── Fallback: parse tool call(s) embedded as JSON in text ──────────
            if not tool_calls and content:
                tool_calls = _extract_text_tool_calls(content)
                if tool_calls:
                    console.print(f" [dim](text-mode)[/dim]")

            if not tool_calls:
                console.print(" [yellow]no tool call[/yellow]")
                if content:
                    console.print(Panel(content[:600], title="Agent text (no files)", border_style="yellow"))
                return None

            console.print()

            # OpenAI protocol: tool_call.function.arguments must be a JSON *string*
            # (we parsed it to a dict for dispatch; re-serialize before echoing back)
            echoed_calls = []
            for tc in tool_calls:
                fn   = tc.get("function", {})
                args = fn.get("arguments", {})
                echoed_calls.append({
                    **tc,
                    "function": {
                        **fn,
                        "arguments": json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else args,
                    },
                })
            messages.append({
                "role":       "assistant",
                "content":    content,
                "tool_calls": echoed_calls,
            })

            for call in tool_calls:
                fn      = call.get("function", {})
                name    = fn.get("name", "")
                args    = fn.get("arguments", {})
                call_id = call.get("id", "")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}

                arg_preview = ", ".join(f"{k}={repr(v)[:60]}" for k, v in args.items())
                console.print(f"  [yellow]⚙[/yellow] [bold]{name}[/bold]({arg_preview})")

                result = dispatch(name, args)

                if (
                    name == "write_files"
                    and isinstance(result, dict)
                    and result.get("status") == "pending_review"
                ):
                    n = len(result.get("files", []))
                    console.print(f"  [green]✓[/green] {n} file(s) ready for PM review")
                    return result

                result_str = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
                tool_msg: dict = {"role": "tool", "content": result_str}
                if call_id:
                    tool_msg["tool_call_id"] = call_id
                messages.append(tool_msg)

        console.print(f"[red]Max rounds ({config.MAX_TOOL_ROUNDS}) reached without write_files.[/red]")
        return None

    def _stream_round(self, messages: list[dict]) -> tuple[dict, str]:
        """
        Run one chat round with streaming output.
        Displays tokens live (refreshing last few lines) via Rich Live.
        Returns (response_dict, full_content).
        """
        accumulated = ""
        final_resp: dict = {}

        with Live("", console=console, refresh_per_second=10, transient=True) as live:
            for chunk, final in self.client.stream_chat(
                messages=messages, tools=TOOL_SPECS, temperature=0.05,
                timeout=config.OLLAMA_TIMEOUT,
            ):
                if final is not None:
                    final_resp = final
                    break
                accumulated += chunk
                # Show up to the last 2 lines of reasoning to give a sense of streaming
                lines = accumulated.strip().splitlines()
                preview = "\n".join(lines[-2:]) if lines else ""
                live.update(Text(f"  {preview}", style="dimitalic"))

        # Permanently print the beginning of the model's reasoning/thought
        clean_accumulated = accumulated.strip()
        if clean_accumulated:
            lines = clean_accumulated.splitlines()
            if lines:
                top_line = lines[0]
                if len(top_line) > 120:
                    top_line = top_line[:117] + "..."
                console.print(f"  {top_line}")


        return final_resp, accumulated

    def _build_prompt(self, task: dict, feedback: str = "", skeleton_files: list[dict] | None = None, previous_files: list[dict] | None = None) -> str:
        labels = ", ".join(task.get("labels", []))
        prompt = (
            f"Task: {task['title']}\n"
            f"Priority: {task['priority']}\n"
            f"Labels: {labels}\n\n"
            f"Description:\n{task.get('description', 'No description.')}\n\n"
            "Instructions:\n"
            "1. Use read_file / list_files / search_code to gather context if needed.\n"
            "2. Implement the task completely and correctly.\n"
            "3. Call write_files with ALL created/modified files and a summary.\n"
        )
        if skeleton_files:
            prompt += f"\nSkeleton files from Architect ({len(skeleton_files)} files):\n"
            for f in skeleton_files:
                prompt += f"\n=== {f['path']} ===\n{f['content']}\n"
            prompt += "\nImplement every TODO in the skeleton files above. Return complete files.\n"
        if previous_files:
            prompt += (
                f"\nYour previous attempt produced {len(previous_files)} file(s). "
                "They are included below — do NOT re-read them from disk, use these versions as your starting point. "
                "Fix only what the reviewer flagged; keep everything else intact:\n"
            )
            for f in previous_files:
                prompt += f"\n=== {f['path']} ===\n{f['content']}\n"
        if feedback:
            prompt += f"\nReviewer feedback to address:\n{feedback}\n"
        return prompt


# ── Text-based tool call extraction ───────────────────────────────────────────

def _extract_text_tool_calls(content: str) -> list[dict]:
    """
    Some models output tool calls as JSON in their text response instead of
    using the native tool_calls field. This function detects and normalises them
    into the same structure as native tool_calls so the rest of the loop works
    identically.

    Handles these patterns:
      • ```json { "name": "...", "arguments": {...} } ```
      • bare JSON objects with "name" + "arguments" keys
      • multiple tool calls in one response
    """
    calls = []

    # 1. Extract all ```json ... ``` code blocks first
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)

    # 2. Also try top-level JSON objects not in code blocks
    # Find all {...} that contain both "name" and "arguments"
    raw_objects = re.findall(r"\{[^`]*?\}", content, re.DOTALL)
    blocks += raw_objects

    seen = set()
    for block in blocks:
        block = block.strip()
        if block in seen:
            continue
        seen.add(block)
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue

        # Single tool call: {"name": "...", "arguments": {...}}
        if isinstance(data, dict) and "name" in data and "arguments" in data:
            calls.append({"function": {"name": data["name"], "arguments": data["arguments"]}})
            continue

        # Wrapped: {"tool_calls": [...]} or {"function": {...}}
        if "tool_calls" in data:
            for tc in data["tool_calls"]:
                fn = tc.get("function", {})
                if fn.get("name") and "arguments" in fn:
                    calls.append({"function": fn})

    return calls
