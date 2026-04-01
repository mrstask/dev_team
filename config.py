"""Dev team configuration — paths, models, API endpoints."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.rule import Rule

# Load .env from the project root
load_dotenv(Path(__file__).parent.parent / ".env", override=True)

# ── Project Paths ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent           # target project root
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

# Source projects to port from (optional, configure per project)
SOURCE_PROJECTS: dict[str, Path] = {}
LANGGRAPH_DASHBOARD = ROOT.parent / "langgraph_dashboard"

# ── Step model/backend configuration ──────────────────────────────────────────
# Edit dev_team/models.json to change which backend and model each step uses.
#
# Supported backends:
#   "claude-code"  — Anthropic API via Claude Code SDK (architect only)
#   "openrouter"   — OpenRouter API  (requires OPENROUTER_API_KEY in .env)
#   "ollama"       — Local Ollama server
#
_MODELS_FILE = Path(__file__).parent / "models.json"
STEPS: dict[str, dict[str, str]] = json.loads(_MODELS_FILE.read_text(encoding="utf-8"))


def step(name: str) -> dict[str, str]:
    """Return the backend/model config for a pipeline step."""
    if name not in STEPS:
        raise KeyError(f"Unknown step '{name}'. Available: {list(STEPS)}")
    return STEPS[name]


# ── Ollama ─────────────────────────────────────────────────────────────────────
OLLAMA_URL    = "http://localhost:11434"
OLLAMA_TIMEOUT = 1200     # seconds per request (20 min)

# ── OpenRouter ─────────────────────────────────────────────────────────────────
# API key stays in .env — never commit it to models.json
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")

# ── Dashboard API ──────────────────────────────────────────────────────────────
DASHBOARD_URL        = "http://localhost:8000/api"
DASHBOARD_PROJECT_ID = 3

# ── Shared Console ────────────────────────────────────────────────────────
console = Console()

# Style per backend for agent header rules
_BACKEND_STYLES: dict[str, str] = {
    "claude-code": "magenta",
    "openrouter":  "cyan",
    "ollama":      "yellow",
}


def print_agent_rule(name: str, step_name: str, *, extra: str = "") -> None:
    """Print a styled Rule header for an agent step."""
    cfg = step(step_name)
    style = _BACKEND_STYLES.get(cfg["backend"], "white")
    parts = f"[bold]{name}[/bold]  ·  {cfg['backend']}  ·  {cfg['model']}"
    if extra:
        parts += f"  ·  {extra}"
    console.print(Rule(parts, style=style))

# ── Agent Behaviour ────────────────────────────────────────────────────────────
MAX_TOOL_ROUNDS = 25       # Max ReAct rounds before giving up

# When True, saves the developer's output files on reviewer rejection and passes
# them back as context on the next retry — developer fixes in-place instead of
# re-writing from scratch.
RETRY_WITH_CONTEXT = True

# Directory where retry context (previous attempt files) is persisted
RETRY_DIR = ROOT / "dev_team" / "_retry"

# Broader context storage — architect output, PM review data, retry files
CONTEXT_DIR = ROOT / "dev_team" / "_context"

# ── Event Loop ────────────────────────────────────────────────────────────
EVENT_LOOP_POLL_INTERVAL = 10   # seconds between dashboard polls
MAX_TASK_RETRIES = 5            # max retries per task before marking failed

# Map task labels → agent role keys
LABEL_TO_ROLE: dict[str, str] = {
    "architect":          "architect:design",
    "developer":          "developer:implement",
    "developer-review":   "developer:review",
    "tester":             "tester:unit-tests",
    "tester-integration": "tester:integration-tests",
    "tester-ci":          "tester:ci",
}
