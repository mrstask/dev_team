"""Agent implementations — Architect, Developer, PM, PMAnalysis, Tester."""
from .developer import DevAgent
from .pm import PMAgent
from .pm_analysis import PMAnalysisAgent
from .tester import TestAgent

# ClaudeAgent requires claude_agent_sdk — import lazily to avoid hard dependency
try:
    from .architect import ClaudeAgent
except ImportError:
    ClaudeAgent = None  # type: ignore[assignment,misc]

__all__ = [
    "ClaudeAgent",
    "DevAgent",
    "PMAgent",
    "PMAnalysisAgent",
    "TestAgent",
]
