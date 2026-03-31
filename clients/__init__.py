"""External API clients — Dashboard, Ollama, OpenRouter, Claude Code SDK."""
from .dashboard_client import DashboardClient
from .ollama_client import OllamaClient
from .openrouter_client import OpenRouterClient

# ClaudeClient requires claude_agent_sdk — import lazily to avoid hard dependency
try:
    from .claude_client import ClaudeClient
except ImportError:
    ClaudeClient = None  # type: ignore[assignment,misc]

__all__ = [
    "ClaudeClient",
    "DashboardClient",
    "OllamaClient",
    "OpenRouterClient",
]
