# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Fully autonomous, event-driven multi-agent orchestration system ("Dev Team") that coordinates LLM agents across different backends (OpenRouter, Ollama, Claude Code SDK) to implement software tasks for a target project. An AI PM agent makes all review/approval decisions — no human in the loop.

## Running

```bash
python main.py              # start the autonomous event loop (default)
python main.py run           # same, with --poll-interval option
python main.py board         # display task board
python main.py kick <id>     # move backlog task → architect + action:todo
python main.py status        # health check (Ollama, OpenRouter, dashboard)
python main.py step <id>     # run one pipeline step for a task
python main.py approve <id>  # approve a human-gated task
python main.py reject <id> "feedback"  # reject a human-gated task
```

Requires a virtual environment (`.venv/`) with `pip install -r requirements.txt`. The Claude Agent SDK (`claude_agent_sdk`) and `python-dotenv` are also needed but not listed in requirements.txt.

## Environment

- `OPENROUTER_API_KEY` — loaded from `.env` (parent project root)
- Ollama must be running at `localhost:11434` (if any step uses ollama backend)
- Dashboard API must be running at `localhost:8000/api`
- Target project root is resolved dynamically per-task from the dashboard project's `root_path` field (via each task's `project_id`). No project-specific config needed in dev_team.

## Architecture

### Multi-Project Support

Dev team is project-agnostic. Each task in the dashboard belongs to a project (`project_id`), and each project has a `root_path` configured in the dashboard UI. When processing a task, the event loop:

1. Reads `project_id` from the task
2. Fetches the project's `root_path` via `DashboardClient.get_project_root()`
3. Wraps all agent dispatch in `project_context(root)` — a thread-local context manager in `core/tools.py`
4. All tool functions (`read_file`, `write_file`, `run_pytest`, etc.) resolve paths against `get_project_root()` instead of a global constant

`DashboardClient` takes only `base_url` (no project_id). `get_tasks()` returns tasks from all projects by default, with an optional `project_id` filter.

### Event Loop (`event_loop.py`)

Stateless polling loop that fetches actionable tasks from the dashboard and dispatches them to agents based on `status + action label`. Processes one task at a time.

### Task Status Flow

```
backlog → architect → develop → testing → done
```

Each active status uses action labels:
- `action:todo` — agent needs to do work
- `action:review` — PM agent needs to review
- `action:await-human` — pipeline paused at human gate (approve/reject via CLI)

### ACE Workflow (Research → Plan → Implement)

The pipeline follows the Advanced Context Engineering pattern to maximise context quality:

1. **Research phase** (pre-architect): `ResearchAgent` explores the codebase and produces a compact structured artifact — relevant files, patterns, data flow, warnings.
2. **Plan phase** (architect): `ArchitectAgent` consumes the research artifact, produces skeleton files AND a structured PLAN section (approach, files, design decisions, verification criteria).
3. **Implement phase** (developer): `DevAgent` receives skeleton files and implements all TODOs.

PM review focuses highest effort on the plan (highest leverage) before evaluating skeleton files and subtasks.

### Agent Pipeline

| Step | Agent | Backend | Trigger |
|---|---|---|---|
| 0 | **ResearchAgent** (pre-architect) | OpenRouter ReAct (read-only) | `architect + action:todo` |
| 1 | **ArchitectAgent** | OpenRouter/Ollama ReAct (or Claude Code SDK if configured) | `architect + action:todo` |
| 2 | **PMAgent** (architect review) | OpenRouter | `architect + action:review` |
| 3 | **DevAgent** (Developer) | OpenRouter ReAct loop | `develop + action:todo` |
| 4 | **ArchitectAgent** (dev-review) + **PMAgent** | OpenRouter | `develop + action:review` |
| 5 | **TestAgent** (tests + CI) | OpenRouter | `testing + action:todo` |
| 6 | **PMAgent** (final review) | OpenRouter | `testing + action:review` |

Backend per step is determined entirely by `models.json` — no LLM-specific logic in agent classes.

### Subtask System

The Architect proposes subtasks in its summary. When PM approves, subtasks are created in the dashboard with `parent_task_id`. When all subtasks reach `done`, the parent auto-completes.

### Project Layout

```
dev_team/
├── main.py              # CLI entry point (click)
├── event_loop.py        # Core autonomous loop, task dispatching, state transitions
├── orchestrator.py      # Board display utility for monitoring
├── config.py            # Constants, LLM config, shared console (no project-specific paths)
├── dtypes.py            # Pydantic models (Task, FileContent, ArchitectResult, etc.) + Status/Action constants
├── models.json          # LLM backend config per step (backend, model, fallback)
│
├── agents/              # Agent implementations
│   ├── architect.py     # ArchitectAgent — LLM-agnostic, skeleton files + PLAN + subtask proposals
│   │                    # (ClaudeAgent is a backward-compatible alias)
│   ├── developer.py     # DevAgent — ReAct loop (implement TODOs in skeleton files)
│   ├── pm.py            # PMAgent — user story, architect/developer/testing reviews, post-mortem analysis
│   ├── tester.py        # TestAgent — generates pytest tests AND runs CI (pytest + pylint)
│   └── research.py      # ResearchAgent — read-only ReAct loop, produces research artifact for Architect
│
├── clients/             # External API clients
│   ├── dashboard_client.py  # HTTPX client for the task dashboard API
│   ├── ollama_client.py     # Ollama REST API client
│   └── openrouter_client.py # OpenRouter API client
│
├── prompts/             # Prompt templates (one file per agent/role)
│   ├── architect.py     # Architect system prompt + ARCHITECT_RESEARCH_CONTEXT template
│   ├── developer.py     # Developer role system prompt
│   ├── research.py      # Research agent system + user prompts
│   ├── tester.py        # Tester role + agent system prompts
│   ├── reviewer.py      # Code reviewer system prompt (used by architect:dev-review)
│   ├── pm.py            # PM review prompts (architect/developer/testing)
│   ├── pm_analysis.py   # PM post-mortem analysis prompts
│   ├── ci.py            # Commit message system prompt
│   ├── staging.py       # Staging instruction for Claude Code SDK backend
│   ├── etl_porter.py    # ETL Porter role system prompt
│   ├── pipeline_builder.py  # Pipeline Builder role system prompt
│   ├── review_engine.py # Review Engine role system prompt
│   ├── vision_embedding.py  # Vision & Embedding role system prompt
│   └── dashboard_builder.py # Dashboard Builder role system prompt
│
├── core/                # Shared infrastructure
│   ├── llm.py           # Client factory, streaming display, JSON parsing
│   ├── react_loop.py    # Shared ReAct loop + text tool call extraction
│   ├── roles.py         # Agent role definitions (imports prompts from prompts/)
│   ├── spec_loader.py   # Loads role specs for agent configuration
│   └── tools.py         # Tool implementations + project_context() thread-local for per-task root resolution
│
├── plans/               # Persistent research docs and implementation plans (from slash commands)
│   └── research/        # Research artifacts created by /research_codebase
│
└── .claude/
    └── commands/        # Claude Code slash commands
        ├── research_codebase.md  # /research_codebase — deep codebase exploration
        ├── create_plan.md        # /create_plan — structured implementation plan
        └── implement_plan.md     # /implement_plan — execute from a plan file
```

### Context Storage (`_context/`) — Typed Artifacts

Agent output is persisted in `_context/{task_id}/` between pipeline stages. Each context file has a **Pydantic-validated schema** — validated on save and load to catch malformed agent output early.

| File | Pydantic Model | Contents |
|---|---|---|
| `research.json` | `ResearchContext` | Relevant files, patterns, data flow, warnings, summary |
| `architect.json` | `ArchitectContext` | Skeleton files, summary, subtask proposals, plan |
| `skeleton_files.json` | `SkeletonContext` | Skeleton files copied to each subtask |
| `developer.json` | `DeveloperContext` | Implementation files and summary |
| `previous_files.json` | `DeveloperContext` | Files from previous attempt (for retry) |
| `testing.json` | `TestingContext` | All files + `CIResult` (enum status) + summary |
| `feedback.json` | `FeedbackContext` | Structured review feedback entries |
| `error.log` | *(plain text)* | Exception traceback |

Context models are defined in `dtypes.py`. Cleared on task completion.

### State Machine & Transitions

Status transitions are enforced via `VALID_TRANSITIONS` in `dtypes.py`. All transitions go through `_move_task(task, new_status)` which validates before calling the dashboard API.

```
backlog   → architect, failed
architect → develop, failed
develop   → testing, failed
testing   → done, develop (retry), failed
done      → (terminal)
failed    → backlog (manual only)
```

### CI — pytest + pylint

`TestAgent.run_ci()` writes files to the target project and runs:
1. **pytest** (`pytest tests/ --tb=short -q`) — gates the commit; failures abort the pipeline
2. **pylint** (advisory only) — reported as warnings but does not block

No tox. Both tools are resolved from `.venv/` via `_find_venv_bin()`.

CI outcomes use `CIStatus` enum: `COMMITTED`, `FAILED`, `COMMIT_FAILED` — no magic strings.

### LLM Backend Configuration (`models.json`)

Each step's backend and model are configured independently. Validated at startup via `ModelsConfig` Pydantic model — invalid backend names, empty models, or missing required steps cause immediate `SystemExit`.

| Step | Backend | Model |
|---|---|---|
| researcher | openrouter | qwen/qwen3-6b-plus:free |
| architect | openrouter | qwen/qwen3-6b-plus:free |
| pm | openrouter | qwen/qwen3-6b-plus:free |
| developer | openrouter | qwen/qwen3-6b-plus:free |
| tester | openrouter | qwen/qwen3-6b-plus:free |

To switch to Ollama or Claude Code SDK for a step, change `"backend"` in `models.json`. No code changes needed.

### Retry Handling

Tracked via `retry:N` labels. Max 5 retries before `failed + error:max-retries`. On PM rejection or agent failure, retry counter increments and task resets to `action:todo`.

Feedback is stored as structured `FeedbackContext` entries in `_context/{task_id}/feedback.json` — each entry records source, stage, retry count, and issues. This replaces the previous pattern of appending free text to the task description.

### Tool State Isolation

The ReAct loop's file accumulator is scoped via `tool_scope()` context manager in `core/tools.py`. Files are always cleaned up on exit — even on exceptions or LLM stalls — preventing cross-task data leaks.
