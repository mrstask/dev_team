# Dev Team — Autonomous Multi-Agent Orchestration System

A fully autonomous, event-driven multi-agent system that coordinates LLM agents to implement software tasks end-to-end. An AI PM agent handles all review and approval decisions — no human required in the default configuration.

## Overview

Dev Team polls a task dashboard and automatically routes tasks through a pipeline: research → architect → developer → tester → done. Each stage is handled by a specialized agent. The system is project-agnostic — multiple target projects can be registered in the dashboard, each with its own root path.

## Prerequisites

- Python 3.11+
- A task dashboard API running at `http://localhost:8000/api` (the [ai-ui](https://github.com/your-org/ai-ui) project)
- An [OpenRouter](https://openrouter.ai/) API key (for the default backend)
- Ollama running at `http://localhost:11434` — only required if any step in `models.json` uses the `ollama` backend

## Setup

### 1. Clone and create a virtual environment

```bash
git clone <repo-url> dev_team
cd dev_team
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
pip install claude-agent-sdk   # not in requirements.txt
```

### 3. Configure environment variables

Create a `.env` file in the `dev_team/` directory (or the parent directory):

```env
OPENROUTER_API_KEY=sk-or-...
```

Optional overrides:

```env
A2A_HOST=127.0.0.1
A2A_PORT=5556
```

### 4. Configure LLM backends

Edit `models.json` to choose the backend and model for each pipeline step:

```json
{
  "researcher": { "backend": "openrouter", "model": "google/gemma-4-26b-a4b-it", "fallback": { "backend": "openrouter", "model": "minimax/minimax-m2.5" } },
  "architect":  { "backend": "openrouter", "model": "google/gemma-4-26b-a4b-it", "fallback": { "backend": "openrouter", "model": "minimax/minimax-m2.5" } },
  "pm":         { "backend": "openrouter", "model": "google/gemma-4-26b-a4b-it", "fallback": { "backend": "openrouter", "model": "minimax/minimax-m2.5" } },
  "developer":  { "backend": "openrouter", "model": "google/gemma-4-26b-a4b-it", "fallback": { "backend": "openrouter", "model": "minimax/minimax-m2.5" } },
  "tester":     { "backend": "openrouter", "model": "google/gemma-4-26b-a4b-it", "fallback": { "backend": "openrouter", "model": "minimax/minimax-m2.5" } }
}
```

Supported backends: `openrouter`, `ollama`, `claude-code`. No code changes needed to switch — edit `models.json` only.

### 5. Verify everything is ready

```bash
python main.py status
```

This checks Ollama availability, OpenRouter key, and dashboard connectivity.

## Running

```bash
# Start the autonomous event loop (default)
python main.py

# Same, with custom poll interval (seconds)
python main.py run --poll-interval 15

# Display the current task board
python main.py board

# Board filtered by status
python main.py board --status develop
```

## Task Lifecycle

Tasks in the dashboard flow through these statuses:

```
backlog → architect → develop → testing → done
         (failed at any stage)
```

### Kick off a task

Tasks start in `backlog`. To begin processing:

```bash
python main.py kick <task_id>
```

This moves the task to `architect + action:todo` and the event loop picks it up automatically.

### Run a single pipeline step

```bash
python main.py step <task_id>
```

Useful for debugging — runs exactly one agent step and exits.

## Human Gates

By default, the pipeline runs fully autonomously. To pause after a specific stage and require human approval, edit `HUMAN_GATES` in `config.py`:

```python
HUMAN_GATES = {
    "architect_output": False,   # pause after architect
    "develop_output":   False,   # pause after developer
    "testing_output":   False,   # pause after tests + CI
}
```

Set any gate to `True` to enable it. When a gate is active, the pipeline pauses with `action:await-human`.

### Human review workflow

```bash
# List all tasks waiting for human review
python main.py pending

# Inspect agent output and spec contract
python main.py review <task_id>

# Approve — continues to next pipeline stage
python main.py approve <task_id>

# Reject with feedback — resets to action:todo for retry
python main.py reject <task_id> "your feedback here"
```

## Pipeline Stages

| Step | Agent | Trigger |
|------|-------|---------|
| 0 | **ResearchAgent** — read-only codebase exploration | `architect + action:todo` |
| 1 | **ArchitectAgent** — skeleton files + plan + subtask proposals | `architect + action:todo` |
| 2 | **PMAgent** — architect review | `architect + action:review` |
| 3 | **DevAgent** — implement TODOs in skeleton files | `develop + action:todo` |
| 4 | **ArchitectAgent** + **PMAgent** — code review | `develop + action:review` |
| 5 | **TestAgent** — generate tests + run pytest + pylint | `testing + action:todo` |
| 6 | **PMAgent** — final review | `testing + action:review` |

## A2A Inspector Integration

Dev Team exposes an A2A (Agent-to-Agent) gateway for observability with A2A Inspector:

```bash
python main.py a2a-server
# or with custom host/port:
python main.py a2a-server --host 0.0.0.0 --port 5556
```

Inter-agent messages are logged to `_a2a/messages.jsonl`.

## Prompt Improvement Suggestions

The PM agent records prompt improvement suggestions after each review cycle. View them with:

```bash
python main.py suggestions                # open suggestions (default)
python main.py suggestions --status all   # all suggestions
python main.py suggestions --status applied
```

## Project Layout

```
dev_team/
├── main.py              # CLI entry point
├── event_loop.py        # Autonomous polling loop + task dispatching
├── orchestrator.py      # Board display utility
├── config.py            # Constants, paths, LLM config, human gates
├── dtypes.py            # Pydantic models + Literal types + state machine
├── models.json          # LLM backend config per step
│
├── agents/              # Agent implementations
│   ├── architect.py     # Skeleton files + plan + subtask proposals
│   ├── developer.py     # ReAct loop — implements TODOs
│   ├── pm.py            # Review, approval, post-mortem
│   ├── tester.py        # pytest + pylint CI runner
│   └── research.py      # Read-only codebase explorer
│
├── clients/             # External API clients
│   ├── dashboard_client.py
│   ├── ollama_client.py
│   └── openrouter_client.py
│
├── core/                # Shared infrastructure
│   ├── llm.py           # Client factory, streaming, JSON parsing
│   ├── react_loop.py    # Shared ReAct loop
│   ├── roles.py         # Agent role definitions
│   ├── spec_loader.py   # Role spec loader
│   └── tools.py         # Tool implementations + project_context + tool_scope
│
├── prompts/             # System prompt templates (one file per agent/role)
├── specs/               # Role spec contracts used during review
├── plans/               # Persistent research docs and implementation plans
│
├── _context/            # Per-task Pydantic-validated context artifacts
├── _a2a/                # A2A message log
├── _retry/              # Retry context (previous attempt files)
└── _logs/               # Agent run logs
```

## Context Artifacts

Between pipeline stages, agent output is persisted in `_context/<task_id>/` as Pydantic-validated JSON:

| File | Contents |
|------|----------|
| `research.json` | Files, patterns, data flow, warnings |
| `architect.json` | Skeleton files, plan, subtask proposals |
| `developer.json` | Implemented files and summary |
| `testing.json` | All files + CI result + summary |
| `feedback.json` | Structured review feedback entries per retry |
| `error.log` | Exception traceback on failure |

Context is cleared automatically when a task reaches `done`.

## Retry Handling

Failed reviews and agent errors increment a `retry:N` label. After 5 retries the task is marked `failed + error:max-retries`. Feedback from each rejection is stored in `feedback.json` and injected into the next attempt's prompt.

When `RETRY_WITH_CONTEXT = True` (default), the developer receives the previous attempt's files and fixes them in-place instead of starting from scratch.

## CI

The `TestAgent` runs two tools against the target project:

1. **pytest** (`pytest tests/ --tb=short -q`) — gates the commit; failures abort the pipeline
2. **pylint** — advisory only; reported as warnings but does not block

Both tools are resolved from the target project's `.venv/` directory.
