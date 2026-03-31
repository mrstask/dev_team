# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Fully autonomous, event-driven multi-agent orchestration system ("Dev Team") that coordinates LLM agents across different backends (Claude Code SDK, OpenRouter, Ollama) to implement software tasks for the `habr-agentic` project. An AI PM agent makes all review/approval decisions — no human in the loop.

## Running

```bash
python main.py              # start the autonomous event loop (default)
python main.py run           # same, with --poll-interval option
python main.py board         # display task board
python main.py kick <id>     # move backlog task → architect + action:todo
python main.py status        # health check (Ollama, OpenRouter, dashboard)
```

Requires a virtual environment (`.venv/`) with `pip install -r requirements.txt`. The Claude Agent SDK (`claude_agent_sdk`) and `python-dotenv` are also needed but not listed in requirements.txt.

## Environment

- `OPENROUTER_API_KEY` — loaded from `habr-agentic/.env` (parent project)
- Ollama must be running at `localhost:11434`
- Dashboard API must be running at `localhost:8000/api` (project ID 3)
- Target project root is `habr-agentic/` (one level up, set in `config.py` as `ROOT`)

## Architecture

### Event Loop (`event_loop.py`)

Stateless polling loop that fetches actionable tasks from the dashboard and dispatches them to agents based on `status + action label`. Processes one task at a time.

### Task Status Flow

```
backlog → architect → develop → testing → done
```

Each active status uses action labels:
- `action:todo` — agent needs to do work
- `action:review` — PM agent needs to review

### Agent Pipeline

| Step | Agent | Backend | Trigger |
|---|---|---|---|
| 1 | **ClaudeAgent** (Architect) | Claude Code SDK | `architect + action:todo` |
| 2 | **PMAgent** (review architect) | OpenRouter | `architect + action:review` |
| 3 | **DevAgent** (Developer) | OpenRouter ReAct loop | `develop + action:todo` |
| 4 | **ReviewerAgent** + **PMAgent** | Ollama + OpenRouter | `develop + action:review` |
| 5 | **TestAgent** + **CIAgent** | Ollama | `testing + action:todo` |
| 6 | **PMAgent** (final review) | OpenRouter | `testing + action:review` |

### Subtask System

The Architect proposes subtasks in its summary. When PM approves, subtasks are created in the dashboard with `parent_task_id`. When all subtasks reach `done`, the parent auto-completes.

### Project Layout

```
dev_team/
├── main.py              # CLI entry point (click)
├── event_loop.py        # Core autonomous loop, task dispatching, state transitions
├── orchestrator.py      # Board display utility for monitoring
├── config.py            # All constants, paths, shared console
├── dtypes.py            # TypedDicts (Task, FileContent, AgentResult) + Status/Action constants
│
├── agents/              # Agent implementations
│   ├── claude_agent.py  # Architect via Claude Code SDK, skeletons + subtask proposals
│   ├── agent.py         # Developer ReAct loop with tools
│   ├── pm_agent.py      # AI PM with review_architect/review_developer/review_testing
│   ├── reviewer.py      # Code reviewer (Ollama)
│   ├── tester.py        # Pytest test generator (Ollama)
│   └── ci_agent.py      # Writes files, runs tox, commits to git
│
├── clients/             # External API clients
│   ├── dashboard_client.py  # HTTPX client for the task dashboard API
│   ├── ollama_client.py     # Ollama REST API client
│   └── openrouter_client.py # OpenRouter API client
│
└── core/                # Shared infrastructure
    ├── llm.py           # Client factory, streaming display, JSON parsing
    ├── react_loop.py    # Shared ReAct loop + text tool call extraction
    ├── roles.py         # Agent role definitions and system prompts
    └── tools.py         # Tool implementations; cross-project reading via prefixes
```

### Context Storage (`_context/`)

Agent output is persisted in `_context/{task_id}/` between pipeline stages. Keys: `architect.json`, `skeleton_files.json`, `developer.json`, `previous_files.json`, `testing.json`. Cleared on task completion.

### Retry Handling

Tracked via `retry:N` labels. Max 5 retries before `failed + error:max-retries`. On PM rejection or agent failure, retry counter increments and task resets to `action:todo` with feedback appended.
