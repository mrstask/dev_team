"""Dev team configuration — paths, models, API endpoints."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (habr-agentic/.env)
load_dotenv(Path(__file__).parent.parent / ".env", override=True)

# ── Project Paths ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent           # habr-agentic/
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

# Source projects to port from
HABR_ADMIN = ROOT.parent / "habr_admin"
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
DASHBOARD_PROJECT_ID = 3   # HAP — Habr Agentic Pipeline

# ── Agent Behaviour ────────────────────────────────────────────────────────────
MAX_TOOL_ROUNDS = 25       # Max ReAct rounds before giving up

# When True, saves the developer's output files on reviewer rejection and passes
# them back as context on the next retry — developer fixes in-place instead of
# re-writing from scratch.
RETRY_WITH_CONTEXT = True

# Directory where retry context (previous attempt files) is persisted
RETRY_DIR = ROOT / "dev_team" / "_retry"

# Map task labels → agent role keys
LABEL_TO_ROLE: dict[str, str] = {
    "architect":        "architect",
    "developer":        "developer",
    "etl-porter":       "etl_porter",
    "pipeline-builder": "pipeline_builder",
    "review-engine":    "review_engine",
    "vision-embedding": "vision_embedding",
    "dashboard-builder":"dashboard_builder",
    "tester":           "tester",
}
