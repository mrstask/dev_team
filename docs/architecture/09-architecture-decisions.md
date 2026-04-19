# 09 — Architecture Decisions

> **arc42 question**: *What significant decisions were made, why, and what are the trade-offs?*

← [[08-crosscutting-concepts]] | Next: [[10-quality-requirements]] →

---

Architecture Decision Records (ADRs) document significant choices. Each ADR answers: **Context** (why was this decision needed?), **Decision** (what was chosen?), **Consequences** (what does this enable, and what does it cost?).

---

## ADR-001: Polling Over Event-Driven

| Field | Detail |
|-------|--------|
| **Status** | Accepted |
| **Date** | Project inception |
| **File** | `event_loop.py`, `config.py` (`EVENT_LOOP_POLL_INTERVAL`) |

**Context**: Dev Team needs to pick up tasks from the Dashboard API. Options were: (a) polling on a fixed interval, (b) long-polling/SSE, (c) webhook/push from Dashboard.

**Decision**: Synchronous polling every 10 seconds.

**Consequences**:
- ✅ Simple — no message broker, no webhook infrastructure, no persistent connections.
- ✅ Easy error recovery — if polling fails, just retry on the next cycle.
- ✅ Stateless loop — no in-memory queue state to lose on crash.
- ❌ Up to 10-second latency between task creation and pickup.
- ❌ Wasted requests when the queue is empty.

---

## ADR-002: Pydantic for Inter-Agent Contracts

| Field | Detail |
|-------|--------|
| **Status** | Accepted |
| **File** | `dtypes.py` |

**Context**: Agent outputs are JSON-serialized Python dicts passed between pipeline stages via the filesystem. Without schema enforcement, a model hallucination or missing key causes a silent bug or cryptic `KeyError` downstream.

**Decision**: All inter-agent data flows through Pydantic v2 models. Results are validated on both save (`model_dump()`) and load (`model_validate()`).

**Consequences**:
- ✅ Schema drift between agents is caught immediately with a meaningful error.
- ✅ Frozen result models (`ConfigDict(frozen=True)`) prevent accidental mutation.
- ✅ `Literal` types (e.g., `CIStatus`) reject invalid values at construction time.
- ❌ Adding a new field to an agent result requires updating the Pydantic model in `dtypes.py`.

---

## ADR-003: `tool_scope()` for File Isolation

| Field | Detail |
|-------|--------|
| **Status** | Accepted |
| **File** | `core/tools.py` (`tool_scope()`, `_rollback_written_files()`) |

**Context**: Agents write files to the target project during their ReAct loop. If an agent fails mid-loop (e.g., hits max rounds without calling `finish()`), those partial files remain on disk and confuse subsequent retry attempts.

**Decision**: A `tool_scope()` context manager tracks all `write_file()` calls in a thread-local list. On failure exit (no `finish()` call), `_rollback_written_files()` deletes every tracked file.

**Consequences**:
- ✅ Each retry starts from a clean state — no leftover partial files.
- ✅ No cross-task leaks when running multiple tasks sequentially.
- ❌ Extra bookkeeping in `write_file()` — every write must register the path.

---

## ADR-004: Shared ReAct Loop for All Agents

| Field | Detail |
|-------|--------|
| **Status** | Accepted |
| **File** | `core/react_loop.py` (`run_react_loop()`) |

**Context**: Each agent needs to call an LLM, parse tool calls, execute tools, and stream output. Duplicating this logic per agent would create 5 divergent implementations.

**Decision**: All agents use the same `run_react_loop()` function. Agent identity is expressed entirely through the system prompt and the tool spec subset passed as arguments.

**Consequences**:
- ✅ Error handling (stall, rate limit, max rounds) implemented once, works for all agents.
- ✅ Streaming display logic is unified.
- ✅ Adding a new agent is as simple as writing a new system prompt and selecting a tool subset.
- ❌ Agents with very different interaction patterns (e.g., PMAgent which does one shot, not a loop) still go through the loop machinery — minor overhead.

---

## ADR-005: Fallback Model on HTTP 429

| Field | Detail |
|-------|--------|
| **Status** | Accepted |
| **File** | `core/llm.py` (`stream_chat_with_display()`), `models.json` |

**Context**: Free-tier models on OpenRouter hit rate limits frequently. A task should not fail just because the primary model is rate-limited.

**Decision**: Each pipeline step can specify a `"fallback"` model in `models.json`. When the primary model returns HTTP 429, `LLMRateLimitError` is raised, and the loop transparently retries with the fallback client.

**Consequences**:
- ✅ Tasks continue through rate limits without operator intervention.
- ✅ Fallback is transparent to the agent — the same tool-call flow continues.
- ❌ Fallback model may be weaker, producing lower-quality output for that request.
- ❌ If both models are rate-limited simultaneously, the loop sleeps and retries — eventual failure is possible.

---

## ADR-006: File Rollback on Agent Failure

| Field | Detail |
|-------|--------|
| **Status** | Accepted |
| **File** | `event_loop.py` (`_rollback_written_files()`) |

**Context**: The Architect and Developer agents write files during their ReAct loop. If they hit max rounds or produce no `finish()` output, those partial files survive on disk.

**Decision**: `_rollback_written_files()` is called in `event_loop.py` whenever an agent loop returns `None` (indicating failure without `finish()`). It deletes all files tracked in `_written_paths`.

**Consequences**:
- ✅ Retry attempts are not confused by leftover files from the previous attempt.
- ✅ The target project filesystem stays clean.
- ❌ Files the agent partially wrote that may have been useful for debugging are lost. (The `error.log` compensates partially.)

---

## ADR-007: Retry With Context

| Field | Detail |
|-------|--------|
| **Status** | Accepted |
| **File** | `config.py` (`RETRY_WITH_CONTEXT`), `agents/developer.py` (`_build_prompt()`) |

**Context**: When PM rejects a developer's output, the developer needs to improve. Option A: restart from skeleton files (clean slate). Option B: receive the previous implementation plus structured feedback.

**Decision**: When `RETRY_WITH_CONTEXT = True` (default), the developer receives:
- The skeleton files (unchanged baseline).
- The previous implementation files (what was tried before).
- Structured `FeedbackContext` entries from all prior rejections.

**Consequences**:
- ✅ Developer can make targeted fixes rather than starting over blindly.
- ✅ Structured feedback (not free text) helps the developer understand specific issues.
- ❌ The prompt grows with each retry — context window pressure increases.
- ❌ Previous files may anchor the model toward the wrong approach.

---

## ADR-008: Human Gates as Opt-In

| Field | Detail |
|-------|--------|
| **Status** | Accepted |
| **File** | `config.py` (`HUMAN_GATES`), `event_loop.py` |

**Context**: The system is designed for full autonomy, but operators may want to inspect output at certain checkpoints (e.g., before the developer starts implementing an architect's plan).

**Decision**: Human gates are disabled by default. The `HUMAN_GATES` dict in `config.py` has three opt-in checkpoints: `architect_output`, `develop_output`, `testing_output`. When enabled, the task gets `action:await-human` and the loop skips it until `approve` or `reject` is called from the CLI.

**Consequences**:
- ✅ Default is fully autonomous — no operator babysitting required.
- ✅ Easy to enable for any stage without code changes.
- ❌ Enabling human gates defeats the autonomy goal — use sparingly.

---

## ADR-009: Backend-Agnostic via `models.json`

| Field | Detail |
|-------|--------|
| **Status** | Accepted |
| **File** | `models.json`, `core/llm.py` (`create_client()`), `config.py` (`ModelsConfig`) |

**Context**: The project started with OpenRouter but needed to support Ollama (local) and Claude Code SDK. Hardcoding backend choice in agent classes would require code changes to switch.

**Decision**: `models.json` is the single source of truth for backend and model per step. `create_client(step_name)` reads this config and returns the appropriate client. `ModelsConfig` validates the config at startup — invalid `backend` values or empty model names cause `SystemExit`.

**Consequences**:
- ✅ Switching a step's backend requires zero code changes.
- ✅ Different steps can use different backends simultaneously.
- ✅ Config errors are caught at startup, not mid-task.
- ❌ Adding a new backend requires changes in `core/llm.py` and `dtypes.py` (`Backend` Literal type).

---

## ADR-010: A2A Protocol for Observability

| Field | Detail |
|-------|--------|
| **Status** | Accepted |
| **File** | `a2a_server.py`, `dtypes.py` (`A2AMessage`), `event_loop.py` |

**Context**: Autonomous pipelines are opaque — it is hard to tell what the agents did, why a task failed, or how to reproduce a run. Logs alone are insufficient for structured replay.

**Decision**: After each agent handoff, Dev Team publishes a structured `A2AMessage` to the A2A gateway. Messages are stored in `_a2a/messages.jsonl`. The gateway is optional — failures are swallowed.

**Consequences**:
- ✅ Full task execution can be reconstructed from the message log.
- ✅ Inspector tools can visualize the agent graph and replay decisions.
- ✅ Non-blocking — no risk of pipeline stalls from observability failures.
- ❌ Adds message construction overhead on every agent transition.
- ❌ Messages can grow large when attachments (file contents) are included.

---

## ADR-011: PM as Autonomous Reviewer

| Field | Detail |
|-------|--------|
| **Status** | Accepted |
| **File** | `agents/pm.py`, `core/llm.py` (`parse_json_response()`) |

**Context**: Review cycles traditionally require a human. Making PM review autonomous is central to the "no human in the loop" goal.

**Decision**: The PM agent reviews output at three stages (architect, develop, testing) using structured prompts from `prompts/pm.py`. Its response is parsed as `ReviewResult` JSON. Three fallback parsing strategies ensure the pipeline doesn't stall if the LLM produces imperfect JSON.

**Consequences**:
- ✅ Fully autonomous — tasks can complete overnight without human attention.
- ✅ PM review quality improves as better models become available (just change `models.json`).
- ❌ PM can approve bad code. Quality of review depends entirely on model capability.
- ❌ Three-strategy JSON parsing is a fragility — it masks model output quality problems.

---

## ADR-012: Context Artifacts Persisted Between Stages

| Field | Detail |
|-------|--------|
| **Status** | Accepted |
| **File** | `event_loop.py` (`_save_typed_context()`, `_load_typed_context()`), `config.py` (`CONTEXT_DIR`) |

**Context**: The pipeline has 6 stages. Each stage's agent needs output from prior stages. Options: (a) pass everything in memory, (b) store in the database, (c) write JSON files to disk.

**Decision**: Persist each stage's output as a validated JSON file in `_context/<task_id>/`. Load via `_load_typed_context()` which calls `model_validate()`.

**Consequences**:
- ✅ Operator can inspect any stage's output for debugging.
- ✅ Pipeline is resumable — if the process crashes between stages, the next run loads from disk.
- ✅ Decoupled from the Dashboard — the dashboard stores task status; the filesystem stores heavy artifacts.
- ❌ Disk space grows linearly with task count (cleared on `done`).
- ❌ JSON files are not transactional — a crash during write could produce a corrupt artifact.
