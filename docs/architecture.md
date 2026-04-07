# Architecture — Autonomous Dev Team

## Overview

The dev team is a fully autonomous, event-driven system that orchestrates multiple LLM agents to implement software tasks for a target project. There is no human in the loop — an AI **PM agent** makes all review and approval decisions.

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
| `action:await-human` | Pipeline paused at a human gate |

The `backlog` and `done` statuses have no action labels. A `failed` status is used for tasks that exceed retry limits or hit unrecoverable errors.

## ACE Workflow (Research → Plan → Implement)

The pipeline follows the Advanced Context Engineering pattern, front-loading codebase understanding to maximise output quality:

```
Research (read-only) → Architect (plan + skeletons) → PM review → Developer (implement) → ...
```

**Leverage hierarchy**: bad research → thousands of bad lines; bad plan → hundreds; bad code → a few.
PM review effort is weighted accordingly — plan quality is evaluated first and most critically.

## Agent Pipeline

### 0. Research (ResearchAgent — pre-architect)

**Trigger**: `architect + action:todo` (runs before ArchitectAgent)

- Read-only ReAct loop using `read_file`, `list_files`, `search_code` tools
- Terminates when agent calls `submit_research` with a JSON artifact
- Artifact contains: `relevant_files`, `patterns`, `data_flow`, `warnings`, `summary`
- Non-blocking — if research fails, architect proceeds without context
- Findings saved to `_context/{task_id}/research.json` and appended to architect's system prompt

### 1. Architect (ArchitectAgent)

**Trigger**: `architect + action:todo`

- LLM-agnostic — backend determined by `models.json` (openrouter/ollama/claude-code)
- Receives research artifact in its system prompt context
- Produces skeleton files with typed signatures, docstrings, and TODO comments
- Produces a structured **PLAN** section before subtasks:
  - `## Approach` — high-level strategy
  - `## Files to Create / Modify` — with rationale
  - `## Key Design Decisions` — trade-offs and constraints
  - `## Verification` — how to know it's correct
- Proposes development subtasks in its summary
- Output saved to `_context/{task_id}/architect.json`
- Transitions to `architect + action:review`

### 2. PM Review — Architect Output

**Trigger**: `architect + action:review`

The PM agent reviews in priority order:
1. **Plan quality** (highest leverage) — approach soundness, design decisions, verification criteria
2. Skeleton file quality (types, imports, structure)
3. Subtask breakdown (focused, independent, well-described)
4. Alignment with the original task spec

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

### 4. Code Review + PM Review — Developer Output

**Trigger**: `develop + action:review`

First, **ArchitectAgent** runs `run_dev_review()` — checks code correctness, conventions, and completeness. If rejected, the task goes back to `develop + action:todo` with feedback.

If the code review passes, **PMAgent** does a strategic review:
- Business logic correctness
- Integration with existing code
- Completeness of implementation

**If approved**: Moves to `testing + action:todo`.
**If rejected**: Back to `develop + action:todo` with PM feedback.

### 5. Testing + CI (TestAgent)

**Trigger**: `testing + action:todo`

- **TestAgent** generates pytest unit tests via ReAct loop
- **TestAgent.run_ci()** writes all files to disk, runs `pytest` then `pylint`, commits on green
  - pytest failures abort the pipeline (task goes back to `develop + action:todo`)
  - pylint issues are advisory warnings only
- Results saved to `_context/{task_id}/testing.json`
- Transitions to `testing + action:review`

### 6. PM Review — Testing Output

**Trigger**: `testing + action:review`

PM reviews test results and CI output (failures-only compacted output):

**If approved and CI committed**: Task moves to `done`. Context is cleared. If this was a subtask, checks if the parent task should also complete.

**If rejected**: Back to `develop + action:todo` with feedback about test failures.

## Parent-Child Task Relationship

The Architect creates subtasks when its output is approved by the PM. Each subtask has a `parent_task_id` linking it to the original task.

When all subtasks of a parent reach `done`, the parent automatically moves to `done` as well. This is checked after every subtask completion and during the idle polling cycle.

## State Machine Validation

Status transitions are enforced via a `VALID_TRANSITIONS` map. Invalid transitions raise a `ValueError` instead of silently corrupting task state.

```
Valid transitions:
  backlog   → architect, failed
  architect → develop, failed
  develop   → testing, failed
  testing   → done, develop (rejection retry), failed
  done      → (terminal)
  failed    → backlog (manual resurrection only)
```

All status transitions in the event loop go through `_move_task(task, new_status)` which validates against this map before calling the dashboard API. This prevents accidental regressions like moving a task from `testing` back to `backlog`.

## CI Result Status

CI outcomes use `CIStatus = Literal["committed", "failed", "commit_failed"]` — validated by Pydantic:

| Value | Meaning |
|---|---|
| `"committed"` | Tests passed, code committed to git |
| `"failed"` | pytest failed, no commit |
| `"commit_failed"` | Tests passed but git commit failed |

The `CIResult.status` field is typed as `CIStatus`, validated on creation in `TestAgent` and on deserialization when loaded from `testing.json`. Invalid values are rejected at construction.

## Retry and Error Handling

- Retries are tracked via `retry:N` labels on each task
- Maximum retries: 5 (configurable via `MAX_TASK_RETRIES`)
- On each PM rejection or agent failure, the retry counter increments
- When the limit is reached, the task moves to `failed + error:max-retries`
- Unhandled exceptions mark the task as `failed + error:exception` with a traceback saved to `_context/{task_id}/error.log`

## Structured Feedback

Review feedback is stored as structured `FeedbackContext` in `_context/{task_id}/feedback.json` instead of being appended as free text to the task description.

Each entry records:
- `source` — who produced the feedback (`pm`, `architect`, `ci`)
- `stage` — which pipeline stage (`testing`, `develop`)
- `retry` — retry count at the time of feedback
- `feedback` — the feedback text
- `issues` — list of specific issues
- `timestamp` — ISO timestamp

This prevents the task description from becoming an unstructured log of mixed spec + feedback + CI output, and gives the developer agent clean, parseable feedback on retry.

## Tool State Isolation

The ReAct loop's file accumulator (`_written_files`) is scoped via a `tool_scope()` context manager. This guarantees that files accumulated during a tool loop are always cleaned up — even if the agent stalls, the LLM times out, or an exception is raised mid-loop. Without this, files from a failed task could silently leak into the next task's output.

## Context Storage — Typed Artifacts

All intermediate agent output is stored in `_context/{task_id}/` between pipeline stages. Each context file has a **Pydantic-validated schema** — data is validated on both save and load, preventing silent corruption from malformed agent output.

| File | Pydantic Model | Contents |
|---|---|---|
| `research.json` | `ResearchContext` | Relevant files, patterns, data flow, warnings, summary |
| `architect.json` | `ArchitectResult` | Skeleton files (`list[FileContent]`), summary, subtask proposals (`list[SubtaskProposal]`), plan |
| `skeleton_files.json` | `list[FileContent]` | Skeleton files copied to each subtask (validated per-item) |
| `developer.json` | `DeveloperResult` | Implementation files (`list[FileContent]`) and summary |
| `previous_files.json` | `list[FileContent]` | Files from previous attempt (for retry, validated per-item) |
| `testing.json` | `TestingContext` | All files + `CIResult` (Literal status) + summary |
| `feedback.json` | `FeedbackContext` | Structured review feedback entries (source, stage, retry count) |
| `error.log` | *(plain text)* | Exception traceback |

Context is cleared when a task reaches `done`.

### Context Validation

Context is loaded from JSON and validated via `Model.model_validate(raw)`:

```python
_save_context(task_id, "architect", result.model_dump())
raw = _load_context(task_id, "architect")
ctx = ArchitectResult.model_validate(raw)   # typed attribute access: ctx.files, ctx.subtasks
```

If a context file fails validation (missing fields, wrong types), Pydantic raises `ValidationError` — caught by the exception handler which marks the task as failed. This replaces the previous pattern of unsafe `ctx["files"]` dict access that would surface as cryptic `KeyError` exceptions.

## LLM Backend Configuration

Each agent uses a backend configured in `models.json`. Switching backends requires only a `models.json` change — no code changes.

| Step | Default Backend | Notes |
|---|---|---|
| researcher | openrouter | Read-only ReAct; submit_research terminal tool |
| architect | openrouter | ReAct or Claude Code SDK (if `"backend": "claude-code"`) |
| pm | openrouter | Streaming chat only; no tool loop |
| developer | openrouter | ReAct with write_files tool |
| tester | openrouter | ReAct for test generation; direct subprocess for CI |

### Config Validation

`models.json` is validated at startup via a `ModelsConfig` Pydantic model. The following checks run before the event loop starts:

- All required steps exist: `researcher`, `architect`, `pm`, `developer`, `tester`
- Backend is one of: `openrouter`, `ollama`, `claude-code`
- Model name is non-empty
- Fallback config (if present) follows the same schema recursively

Invalid configuration causes an immediate `SystemExit` with a clear validation error — no LLM tokens are wasted on a misconfigured run.

## Slash Commands (`.claude/commands/`)

Three commands support manual ACE-style workflows in Claude Code:

| Command | Purpose |
|---|---|
| `/research_codebase` | Deep codebase exploration → saves to `plans/research/` |
| `/create_plan` | Structured implementation plan from research → saves to `plans/` |
| `/implement_plan` | Execute a plan file using the autonomous pipeline |

## CLI Commands

```bash
python main.py              # start the autonomous event loop
python main.py run           # same, with --poll-interval option
python main.py board         # display task board
python main.py kick <id>     # move backlog task → architect + action:todo
python main.py status        # health check
python main.py step <id>     # run one pipeline step
python main.py approve <id>  # approve a human gate
python main.py reject <id> "feedback"  # reject with feedback
```
