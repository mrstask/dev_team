"""Shared LLM utilities — client factory, streaming display, JSON parsing."""
import json
import re

from rich.live import Live
from rich.text import Text

import config
from clients import OllamaClient, OpenRouterClient


# ── Client factory ────────────────────────────────────────────────────────────

def create_client(step_name: str) -> OllamaClient | OpenRouterClient:
    """Create an LLM client for the given pipeline step (from models.json)."""
    s = config.step(step_name)
    backend = s["backend"]
    model = s["model"]

    if backend == "openrouter":
        if not config.OPENROUTER_API_KEY:
            raise ValueError("OPENROUTER_API_KEY is not set in .env")
        return OpenRouterClient(config.OPENROUTER_API_KEY, model)
    if backend == "ollama":
        return OllamaClient(config.OLLAMA_URL, model)
    raise ValueError(f"Unknown backend '{backend}' for step '{step_name}'")


# ── Streaming display ─────────────────────────────────────────────────────────

def stream_chat_with_display(
    client: OllamaClient | OpenRouterClient,
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    temperature: float = 0.05,
    timeout: int = 600,
    preview_lines: int = 2,
) -> tuple[dict, str]:
    """
    Stream an LLM chat response with a Rich Live preview.

    Returns (response_dict, accumulated_content).
    """
    console = config.console
    accumulated = ""
    final_resp: dict = {}

    with Live("", console=console, refresh_per_second=10, transient=True) as live:
        for chunk, final in client.stream_chat(
            messages=messages,
            tools=tools,
            temperature=temperature,
            timeout=timeout,
        ):
            if final is not None:
                final_resp = final
                break
            accumulated += chunk
            lines = accumulated.strip().splitlines()
            preview = "\n".join(lines[-preview_lines:]) if lines else ""
            live.update(Text(f"  {preview}", style="dim italic"))

    return final_resp, accumulated


# ── JSON response parsing ─────────────────────────────────────────────────────

def parse_json_response(content: str) -> dict:
    """Parse a JSON object from LLM response text.

    Tries three strategies:
      1. Direct JSON parse
      2. Regex extract first {...} block
      3. Heuristic fallback (approved/rejected based on keywords)
    """
    content = content.strip()

    # 1. Direct parse
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # 2. Extract first {...} block
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # 3. Heuristic fallback
    lower = content.lower()
    approved = any(kw in lower for kw in ("approved", "looks good", "lgtm", "no issues"))
    return {
        "approved": approved,
        "issues": [] if approved else ["LLM returned unparseable response"],
        "overall_comment": content[:300],
        "feedback": "" if approved else content[:300],
    }
