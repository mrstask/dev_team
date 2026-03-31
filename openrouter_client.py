"""OpenRouter API client — OpenAI-compatible with streaming and tool calling."""
import json
import datetime
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx

_BASE_URL = "https://openrouter.ai/api/v1"
_LOG_DIR  = Path(__file__).parent / "_logs"


def _dump_debug_log(payload: dict, status: int, body: str) -> Path:
    """Write request + error response to a timestamped JSON file for inspection."""
    _LOG_DIR.mkdir(exist_ok=True)
    ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = _LOG_DIR / f"openrouter_{ts}.json"
    log_path.write_text(
        json.dumps({"request": payload, "response_status": status, "response_body": body},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return log_path


class OpenRouterClient:
    def __init__(self, api_key: str, model: str, site_url: str = "", site_name: str = "habr-agentic"):
        self.api_key   = api_key
        self.model     = model
        self.site_url  = site_url
        self.site_name = site_name

    def _headers(self) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json",
            "X-Title":       self.site_name,
        }
        if self.site_url:
            h["HTTP-Referer"] = self.site_url
        return h

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.05,
        timeout: int = 1200,
    ) -> dict:
        """Blocking chat call. Returns a normalised response dict matching Ollama's shape."""
        payload: dict[str, Any] = {
            "model":       self.model,
            "messages":    messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools

        with httpx.Client(timeout=timeout) as client:
            resp = client.post(f"{_BASE_URL}/chat/completions", json=payload, headers=self._headers())
            if resp.status_code >= 400:
                _dump_debug_log(payload, resp.status_code, resp.text)
                resp.raise_for_status()
            data = resp.json()

        return self._normalise(data)

    def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.05,
        timeout: int = 1200,
    ) -> Iterator[tuple[str, dict | None]]:
        """
        Streaming chat. Yields (chunk, None) for each text token and
        ("", response_dict) as the final item when the stream ends.
        response_dict matches Ollama's shape so DevAgent can use it unchanged.
        """
        payload: dict[str, Any] = {
            "model":       self.model,
            "messages":    messages,
            "temperature": temperature,
            "stream":      True,
        }
        if tools:
            payload["tools"] = tools

        full_content: str = ""
        tool_calls_acc: dict[int, dict] = {}  # index → accumulated call

        with httpx.Client(timeout=timeout) as client:
            with client.stream(
                "POST", f"{_BASE_URL}/chat/completions",
                json=payload, headers=self._headers(),
            ) as resp:
                if resp.status_code >= 400:
                    body = resp.read().decode("utf-8", errors="replace")
                    log_path = _dump_debug_log(payload, resp.status_code, body)
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code} — logged to {log_path}",
                        request=resp.request,
                        response=resp,
                    )
                for line in resp.iter_lines():
                    line = line.strip()
                    if not line or line == "data: [DONE]":
                        continue
                    if line.startswith("data: "):
                        line = line[6:]
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    delta = data.get("choices", [{}])[0].get("delta", {})
                    finish = data.get("choices", [{}])[0].get("finish_reason")

                    # Accumulate text
                    chunk = delta.get("content") or ""
                    if chunk:
                        full_content += chunk
                        yield chunk, None

                    # Accumulate tool call deltas
                    for tc_delta in delta.get("tool_calls", []):
                        idx = tc_delta.get("index", 0)
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                "id":       tc_delta.get("id", ""),
                                "type":     "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        fn = tc_delta.get("function", {})
                        tool_calls_acc[idx]["function"]["name"]      += fn.get("name", "")
                        tool_calls_acc[idx]["function"]["arguments"] += fn.get("arguments", "")
                        if tc_delta.get("id"):
                            tool_calls_acc[idx]["id"] = tc_delta["id"]

                    if finish in ("stop", "tool_calls", "length"):
                        # Parse accumulated tool call arguments
                        tool_calls = list(tool_calls_acc.values())
                        for tc in tool_calls:
                            raw = tc["function"]["arguments"]
                            try:
                                tc["function"]["arguments"] = json.loads(raw)
                            except json.JSONDecodeError:
                                tc["function"]["arguments"] = {}

                        final_response: dict = {
                            "model": self.model,
                            "message": {
                                "role":       "assistant",
                                "content":    full_content,
                                "tool_calls": tool_calls,
                            },
                            "done": True,
                        }
                        yield "", final_response
                        return

    def _normalise(self, data: dict) -> dict:
        """Convert OpenAI response shape → Ollama-compatible shape for DevAgent."""
        choice  = data.get("choices", [{}])[0]
        msg     = choice.get("message", {})
        content = msg.get("content") or ""

        # Parse tool calls
        raw_tcs    = msg.get("tool_calls") or []
        tool_calls = []
        for tc in raw_tcs:
            fn   = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            tool_calls.append({
                "id":       tc.get("id", ""),
                "type":     "function",
                "function": {"name": fn.get("name", ""), "arguments": args},
            })

        return {
            "model": data.get("model", self.model),
            "message": {
                "role":       "assistant",
                "content":    content,
                "tool_calls": tool_calls,
            },
            "done": True,
        }
