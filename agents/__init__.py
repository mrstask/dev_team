"""Agent implementations — Architect, CI, Developer, PM, Reviewer, Tester."""
from .ci import CIAgent
from .developer import DevAgent
from .pm import PMAgent
from .reviewer import ReviewerAgent
from .tester import TestAgent

# ClaudeAgent requires claude_agent_sdk — import lazily to avoid hard dependency
try:
    from .architect import ClaudeAgent
except ImportError:
    ClaudeAgent = None  # type: ignore[assignment,misc]

__all__ = [
    "CIAgent",
    "ClaudeAgent",
    "DevAgent",
    "PMAgent",
    "ReviewerAgent",
    "TestAgent",
]
