"""Dev team configuration — paths, models, API endpoints."""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.rule import Rule

# Load .env from dev_team directory, then fall back to parent project root
_here = Path(__file__).parent
load_dotenv(_here / ".env", override=True)
load_dotenv(_here.parent / ".env")

# ── Project Paths ──────────────────────────────────────────────────────────────
# ROOT is a legacy fallback — the pipeline resolves project root dynamically
# from the dashboard project's root_path field via each task's project_id.
ROOT = Path(os.getenv("PROJECT_ROOT", Path(__file__).parent.parent / "habr-agentic"))

# Source projects to port from (optional, configure per project)
SOURCE_PROJECTS: dict[str, Path] = {}
LANGGRAPH_DASHBOARD = Path(os.getenv("LANGGRAPH_DASHBOARD", ROOT.parent / "langgraph_dashboard"))

# ── Step model/backend configuration ──────────────────────────────────────────
# Edit dev_team/models.json to change which backend and model each step uses.
#
# Supported backends:
#   "claude-code"  — Anthropic API via Claude Code SDK (architect only)
#   "openrouter"   — OpenRouter API  (requires OPENROUTER_API_KEY in .env)
#   "ollama"       — Local Ollama server
#
_MODELS_FILE = Path(__file__).parent / "models.json"
_models_raw = json.loads(_MODELS_FILE.read_text(encoding="utf-8"))

# Validate models.json at startup — fail fast on invalid config
try:
    from dtypes import ModelsConfig
    ModelsConfig.model_validate(_models_raw)
except Exception as e:
    print(f"FATAL: Invalid models.json: {e}")
    sys.exit(1)

STEPS: dict[str, dict[str, str]] = _models_raw


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
DASHBOARD_URL = "http://localhost:8000/api"

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
MAX_TOOL_ROUNDS = 100       # Max ReAct rounds before giving up
LLM_STALL_TIMEOUT = 180    # Seconds to wait for any chunk before retrying the request
LLM_STALL_MAX_RETRIES = 3  # Max stall retries per round before giving up

# When True, saves the developer's output files on reviewer rejection and passes
# them back as context on the next retry — developer fixes in-place instead of
# re-writing from scratch.
RETRY_WITH_CONTEXT = True

# Directory where retry context (previous attempt files) is persisted
RETRY_DIR = _here / "_retry"

# Broader context storage — architect output, PM review data, retry files
CONTEXT_DIR = _here / "_context"

# ── Event Loop ────────────────────────────────────────────────────────────
EVENT_LOOP_POLL_INTERVAL = 10   # seconds between dashboard polls
MAX_TASK_RETRIES = 5            # max retries per task before marking failed

# ── Human Gates ───────────────────────────────────────────────────────────
# Set a gate to True to pause the pipeline after that stage and wait for
# human approve/reject via: python main.py review/approve/reject <task_id>
# Set to False to let the pipeline proceed autonomously through that stage.
HUMAN_GATES: dict[str, bool] = {
    "architect_output": False,  # pause after architect, before PM review
    "develop_output":   False,  # pause after developer, before code review + PM
    "testing_output":   False,  # pause after tests + CI, before PM final review
}

# ── Specs ─────────────────────────────────────────────────────────────────
SPECS_DIR = _here / "specs"

# Map task labels → agent role keys
LABEL_TO_ROLE: dict[str, str] = {
    "architect":          "architect:design",
    "developer":          "developer:implement",
    "developer-review":   "developer:review",
    "tester":             "tester:unit-tests",
    "tester-integration": "tester:integration-tests",
    "tester-ci":          "tester:ci",
}
