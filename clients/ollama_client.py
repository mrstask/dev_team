"""Ollama API wrapper with tool calling support."""
import json
from collections.abc import Iterator
from typing import Any

import httpx


class OllamaClient:
    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.05,
        timeout: int = 600,
    ) -> dict:
        """Single blocking chat call. Returns full Ollama response dict."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if tools:
            payload["tools"] = tools

        with httpx.Client(timeout=timeout) as client:
            resp = client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
            return resp.json()

    def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.05,
        timeout: int = 600,
    ) -> Iterator[tuple[str, dict | None]]:
        """
        Streaming chat call. Yields (chunk, None) for each token chunk and
        (final_text, response_dict) as the last item when the stream completes.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": temperature},
        }
        if tools:
            payload["tools"] = tools

        with httpx.Client(timeout=timeout) as client:
            with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as resp:
                resp.raise_for_status()
                full_content: str = ""
                final_response: dict | None = None
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    chunk = data.get("message", {}).get("content", "")
                    if chunk:
                        full_content += chunk
                        yield chunk, None
                    if data.get("done"):
                        # Build a non-streaming-style response dict
                        final_response = {
                            "model": data.get("model", self.model),
                            "message": {
                                "role": "assistant",
                                "content": full_content,
                                "tool_calls": data.get("message", {}).get("tool_calls") or [],
                            },
                            "done": True,
                        }
                        yield "", final_response

    def available_models(self) -> list[str]:
        with httpx.Client(timeout=10) as client:
            resp = client.get(f"{self.base_url}/api/tags")
            resp.raise_for_status()
            return [m["name"] for m in resp.json().get("models", [])]

    def is_model_available(self, model: str) -> bool:
        available = self.available_models()
        base_name = model.split(":")[0]
        return any(base_name in m for m in available)

    def is_alive(self) -> bool:
        try:
            with httpx.Client(timeout=5) as client:
                client.get(f"{self.base_url}/api/tags")
            return True
        except Exception:
            return False
