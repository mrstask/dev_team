"""Shared LLM utilities — client factory, streaming display, JSON parsing."""
import json
import re
import threading
import time

from rich.live import Live
from rich.text import Text

import httpx

import config
from clients import ClaudeClient, OllamaClient, OpenRouterClient


class LLMStallError(Exception):
    """Raised when the LLM produces no output within LLM_STALL_TIMEOUT seconds."""

class LLMRateLimitError(Exception):
    """Raised on HTTP 429 — caller should back off and retry."""
    def __init__(self, retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__(f"rate limited — retry after {retry_after}s")


# ── Client factory ────────────────────────────────────────────────────────────

def create_client(step_name: str) -> ClaudeClient | OllamaClient | OpenRouterClient:
    """Create the primary LLM client for the given pipeline step (from models.json)."""
    return _build_client(config.step(step_name))


def create_fallback_client(step_name: str) -> ClaudeClient | OllamaClient | OpenRouterClient | None:
    """Create the fallback LLM client for the given step, or None if no fallback configured."""
    fb = config.step(step_name).get("fallback")
    return _build_client(fb) if fb else None


def _build_client(cfg: dict) -> ClaudeClient | OllamaClient | OpenRouterClient:
    backend = cfg["backend"]
    model = cfg["model"]
    if backend == "openrouter":
        if not config.OPENROUTER_API_KEY:
            raise ValueError("OPENROUTER_API_KEY is not set in .env")
        return OpenRouterClient(config.OPENROUTER_API_KEY, model)
    if backend == "ollama":
        return OllamaClient(config.OLLAMA_URL, model)
    if backend == "claude-code":
        return ClaudeClient(console=config.console)
    raise ValueError(f"Unknown backend '{backend}'")


# ── Streaming display ─────────────────────────────────────────────────────────

def stream_chat_with_display(
    client: OllamaClient | OpenRouterClient,
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    temperature: float = 0.05,
    preview_lines: int = 10,
    fallback_client: OllamaClient | OpenRouterClient | None = None,
) -> tuple[dict, str]:
    """
    Stream an LLM chat response with a Rich Live preview.

    On HTTP 429, switches transparently to fallback_client if provided.
    Returns (response_dict, accumulated_content).
    """
    try:
        return _stream_once(client, messages, tools=tools, temperature=temperature, preview_lines=preview_lines)
    except LLMRateLimitError:
        if fallback_client is None:
            raise
        model_name = getattr(fallback_client, "model", "fallback")
        config.console.print(f"  [yellow]Rate limited — switching to fallback model ({model_name})[/yellow]")
        return _stream_once(fallback_client, messages, tools=tools, temperature=temperature, preview_lines=preview_lines)


def _stream_once(
    client: OllamaClient | OpenRouterClient,
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    temperature: float = 0.05,
    preview_lines: int = 10,
) -> tuple[dict, str]:
    """Single streaming attempt — raises LLMStallError or LLMRateLimitError on failure."""
    console = config.console
    accumulated = ""
    final_resp: dict = {}
    start = time.monotonic()
    first_token = False

    def _elapsed() -> str:
        return f"{time.monotonic() - start:.0f}s"

    with Live("", console=console, refresh_per_second=4, transient=False) as live:
        stop_ticker = threading.Event()

        def _ticker():
            while not stop_ticker.is_set():
                if not first_token:
                    live.update(Text(f"  [waiting for model… {_elapsed()}]", style="dim yellow"))
                stop_ticker.wait(1)

        ticker_thread = threading.Thread(target=_ticker, daemon=True)
        ticker_thread.start()

        try:
            for chunk, final in client.stream_chat(
                messages=messages,
                tools=tools,
                temperature=temperature,
            ):
                if final is not None:
                    final_resp = final
                    break
                if chunk:
                    if not first_token:
                        first_token = True
                        stop_ticker.set()
                        console.print(f"  [dim]← first token after {_elapsed()}[/dim]")
                    accumulated += chunk
                    lines = accumulated.strip().splitlines()
                    preview = "\n".join(lines[-preview_lines:]) if lines else ""
                    live.update(Text(f"  {preview}", style="dim italic"))
        except httpx.ReadTimeout:
            elapsed = _elapsed()
            stop_ticker.set()
            raise LLMStallError(f"no chunk received for {config.LLM_STALL_TIMEOUT}s (total elapsed: {elapsed})")
        except httpx.HTTPStatusError as e:
            stop_ticker.set()
            if e.response.status_code == 429:
                retry_after = int(e.response.headers.get("Retry-After", 60))
                raise LLMRateLimitError(retry_after)
            raise
        finally:
            stop_ticker.set()
            ticker_thread.join(timeout=2)

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
