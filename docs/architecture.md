# Architecture — Autonomous Dev Team

## Overview

The dev team is a fully autonomous, event-driven system that orchestrates multiple LLM agents to implement software tasks for the **Habr Agentic Pipeline** project. There is no human in the loop — an AI **PM agent** makes all review and approval decisions.

## Event-Driven Design

The system is built around a **stateless polling loop** (`event_loop.py`) that:

1. Polls the task dashboard for tasks with actionable labels
2. Dispatches each task to the appropriate agent based on `status + action label`
3. Processes one task at a time (sequential, no concurrency)
4. Sleeps between polls when no work is available

Tasks are the only shared state. The loop is stateless — it can be stopped and restarted at any time without losing progress.

## Task Statuses and Action Labels

Tasks flow through these statuses:

```
backlog → architect → develop → testing → done
```

Each active status has an **action label** that determines what happens next:

| Label | Meaning |
|---|---|
| `action:todo` | Work needs to be done by an agent |
| `action:review` | Work is done, needs PM review |

The `backlog` and `done` statuses have no action labels. A `failed` status is used for tasks that exceed retry limits or hit unrecoverable errors.

## Agent Pipeline

### 1. Architect (ClaudeAgent — Claude Code SDK)

**Trigger**: `architect + action:todo`

- Reads the task spec and existing codebase
- Produces skeleton files with typed signatures, docstrings, and TODO comments
- Proposes development subtasks in its summary
- Output saved to `_context/{task_id}/architect.json`
- Transitions to `architect + action:review`

### 2. PM Review — Architect Output

**Trigger**: `architect + action:review`

The PM agent reviews:
- Skeleton file quality (types, imports, structure)
- Subtask breakdown (focused, independent, well-described)
- Alignment with the original task spec

**If approved**: Creates subtasks in the dashboard (`develop + action:todo`), each linked to the parent via `parent_task_id`. Skeleton files are saved as context for each subtask.

**If rejected**: Appends feedback to the task description, increments retry count, sets `architect + action:todo` for another attempt.

### 3. Developer (DevAgent — OpenRouter ReAct loop)

**Trigger**: `develop + action:todo`

- Receives skeleton files from the architect context
- Implements all TODOs using a ReAct loop (up to 25 rounds)
- Tools: `read_file`, `list_files`, `search_code`, `write_files`
- On retry: receives previous files + feedback instead of skeletons
- Output saved to `_context/{task_id}/developer.json`
- Transitions to `develop + action:review`

### 4. PM Review — Developer Output

**Trigger**: `develop + action:review`

First, the **ReviewerAgent** (Ollama) checks code correctness, conventions, and completeness. If the reviewer rejects, the task goes back to `develop + action:todo` with feedback.

If the reviewer approves, the **PM agent** does a strategic review:
- Business logic correctness
- Integration with existing code
- Completeness of implementation

**If approved**: Moves to `testing + action:todo`.
**If rejected**: Back to `develop + action:todo` with PM feedback.

### 5. Testing + CI

**Trigger**: `testing + action:todo`

- **TestAgent** (Ollama) generates pytest tests
- **CIAgent** (Ollama) writes all files to disk, runs `tox`, commits on green
- Results saved to `_context/{task_id}/testing.json`
- Transitions to `testing + action:review`

### 6. PM Review — Testing Output

**Trigger**: `testing + action:review`

PM reviews test results and CI output:

**If approved and CI committed**: Task moves to `done`. Context is cleared. If this was a subtask, checks if the parent task should also complete.

**If rejected**: Back to `develop + action:todo` with feedback about test failures.

## Parent-Child Task Relationship

The Architect creates subtasks when its output is approved by the PM. Each subtask has a `parent_task_id` linking it to the original task.

When all subtasks of a parent reach `done`, the parent automatically moves to `done` as well. This is checked after every subtask completion and during the idle polling cycle.

## Retry and Error Handling

- Retries are tracked via `retry:N` labels on each task
- Maximum retries: 5 (configurable via `MAX_TASK_RETRIES`)
- On each PM rejection or agent failure, the retry counter increments
- When the limit is reached, the task moves to `failed + error:max-retries`
- Unhandled exceptions mark the task as `failed + error:exception` with a traceback saved to `_context/{task_id}/error.log`

## Context Storage

All intermediate agent output is stored in `_context/{task_id}/`:

| File | Contents |
|---|---|
| `architect.json` | Skeleton files, summary, subtask proposals |
| `skeleton_files.json` | Skeleton files copied to each subtask |
| `developer.json` | Implementation files and summary |
| `previous_files.json` | Files from previous attempt (for retry) |
| `testing.json` | All files + CI result |
| `error.log` | Exception traceback |

Context is cleared when a task reaches `done`.

## LLM Backend Configuration

Each agent uses a different LLM backend, configured in `models.json`:

| Step | Backend | Default Model |
|---|---|---|
| architect | claude-code | claude-opus-4-6 |
| pm | openrouter | anthropic/claude-sonnet-4 |
| developer | openrouter | xiaomi/mimo-v2-flash |
| reviewer | ollama | devstral-small-2 |
| tester | ollama | devstral-small-2 |
| ci | ollama | devstral-small-2 |

## CLI Commands

```bash
python main.py              # start the autonomous event loop
python main.py run           # same, with --poll-interval option
python main.py board         # display task board
python main.py kick <id>     # move backlog task → architect + action:todo
python main.py status        # health check
```
