"""Agent implementations — Dev, Claude, CI, PM, Reviewer, Tester."""
from .agent import DevAgent
from .ci_agent import CIAgent
from .pm_agent import PMAgent
from .reviewer import ReviewerAgent
from .tester import TestAgent

# ClaudeAgent requires claude_agent_sdk — import lazily to avoid hard dependency
try:
    from .claude_agent import ClaudeAgent
except ImportError:
    ClaudeAgent = None  # type: ignore[assignment,misc]

__all__ = [
    "DevAgent",
    "CIAgent",
    "ClaudeAgent",
    "PMAgent",
    "ReviewerAgent",
    "TestAgent",
]
