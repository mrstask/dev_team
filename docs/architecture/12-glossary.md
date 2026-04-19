# 12 — Glossary

> **arc42 question**: *What do the domain-specific terms mean?*

← [[11-risks-technical-debt]] | [[00-index|Back to Index]] →

---

The glossary defines terms used throughout this documentation and in the codebase. Terms are grouped by domain. Each entry includes the definition and the primary code location.

---

## Group 1: Agent Terms

| Term | Definition | Code location |
|------|-----------|--------------|
| **ResearchAgent** | Read-only agent that explores the target codebase before the architect begins. Produces a `ResearchContext` artifact. | `agents/research.py` |
| **ArchitectAgent** | Design agent that consumes research context and produces skeleton files, a structured PLAN, and subtask proposals. Can use either Claude Code SDK or ReAct loop backend. | `agents/architect.py` |
| **DevAgent** | Implementation agent that fills in the skeleton files produced by the Architect. Receives previous implementation and structured feedback on retries. | `agents/developer.py` |
| **TestAgent** | Testing agent that generates pytest tests AND runs CI (pytest + pylint + git commit). Has an internal iterative fix loop for failing tests. | `agents/tester.py` |
| **PMAgent** | Autonomous reviewer — acts as a Product Manager. Reviews architect output, developer output, and test results. Makes approve/reject decisions without human input. | `agents/pm.py` |
| **ReAct loop** | Reasoning + Acting loop: the shared LLM interaction pattern used by all agents. Alternates between LLM reasoning and tool execution. Implemented as `run_react_loop()`. | `core/react_loop.py` |
| **skeleton files** | Function stubs with type hints and docstrings (but no implementation) produced by the Architect. Marked with `# TODO` placeholders for the Developer to fill in. | `agents/architect.py`, `dtypes.py` (`FileContent`) |
| **role** | A named configuration (system prompt + pipeline step) that defines an agent's persona and purpose. 13 roles defined in `ROLES` dict. | `core/roles.py` |
| **tool spec** | An OpenAI-compatible function definition (`{"name": ..., "description": ..., "parameters": {...}}`) that tells the LLM what tools are available. | `core/tools.py` (`TOOL_SPECS`, etc.) |
| **finish()** | The terminal tool agents call to signal task completion. Returns all written files to the event loop. Calling `finish()` is what distinguishes a successful run from a stalled one. | `core/tools.py` |
| **submit_research()** | The terminal tool for ResearchAgent — equivalent of `finish()` but returns a `ResearchContext` structure. | `core/tools.py` |
| **auto-extract (research)** | Fallback behavior: if ResearchAgent fails to call `submit_research()`, the event loop auto-constructs a `ResearchContext` from the agent's file read history. | `agents/research.py` |

---

## Group 2: Pipeline Terms

| Term | Definition | Code location |
|------|-----------|--------------|
| **action label** | A task label that tells the event loop what to do next. Three values: `action:todo` (agent should work), `action:review` (PM should review), `action:await-human` (wait for human). | `dtypes.py` |
| **action:todo** | Label that triggers agent execution on the next poll. The event loop dispatches the appropriate agent based on the task's `status` field. | `event_loop.py` |
| **action:review** | Label that triggers PM review on the next poll. No agent code is run — just a PM LLM call. | `event_loop.py` |
| **action:await-human** | Label that pauses the pipeline. The event loop skips tasks with this label. Operator must `approve` or `reject` via CLI to resume. | `event_loop.py`, `config.py` |
| **human gate** | An optional pause point in the pipeline. Enabled in `config.py` via `HUMAN_GATES` dict. Three checkpoints: `architect_output`, `develop_output`, `testing_output`. | `config.py` |
| **state machine** | The set of valid task status transitions: `backlog → architect → develop → testing → done`. Enforced via `VALID_TRANSITIONS` dict — invalid transitions raise `ValueError`. | `dtypes.py` |
| **status** | The pipeline stage a task is in. One of: `backlog`, `architect`, `develop`, `testing`, `done`, `failed`. | `dtypes.py` (`TaskStatusName`) |
| **subtask** | A child task created by the event loop based on `SubtaskProposal` entries in the Architect's output. Has `parent_task_id` set and starts at `develop + action:todo`. | `dtypes.py` (`SubtaskProposal`), `event_loop.py` |
| **retry:N** | A task label tracking how many times a task has been rejected and retried. Incremented on every PM rejection. When N reaches `MAX_TASK_RETRIES` (5), the task is failed. | `event_loop.py` |
| **error:max-retries** | Label added when a task exhausts its retry budget. Signals that the task needs human attention. | `event_loop.py` |
| **error:exception** | Label added when an unhandled exception occurs during a task. The stack trace is saved to `_context/<id>/error.log`. | `event_loop.py` |
| **ACE pattern** | Advanced Context Engineering: Research → Plan → Implement. A workflow that maximizes LLM output quality by giving each agent a focused, high-quality context artifact from the previous stage. | `event_loop.py` (pipeline structure) |
| **CI** | Continuous Integration step run by TestAgent: write files to disk → pytest → pylint (advisory) → git commit. | `agents/tester.py` (`run_ci()`) |

---

## Group 3: LLM / Backend Terms

| Term | Definition | Code location |
|------|-----------|--------------|
| **backend** | The LLM provider for a given pipeline step. One of `"openrouter"`, `"ollama"`, `"claude-code"`. Configured in `models.json`. | `models.json`, `dtypes.py` (`Backend`) |
| **primary model** | The first LLM tried for a given pipeline step. Specified in `models.json` `"model"` field. | `models.json` |
| **fallback model** | A secondary LLM used when the primary returns HTTP 429 (rate limit). Specified in `models.json` `"fallback"` field. Optional. | `models.json`, `core/llm.py` |
| **LLMStallError** | Exception raised when the LLM produces no tokens for `LLM_STALL_TIMEOUT` seconds (default: 1200s). Triggers retry up to `LLM_STALL_MAX_RETRIES` times. | `core/llm.py` |
| **LLMRateLimitError** | Exception raised on HTTP 429 from the LLM provider. Triggers transparent switch to the fallback model. | `core/llm.py` |
| **streaming** | The LLM response is delivered incrementally (token by token) and displayed live in the terminal. First-token latency is reported per round. | `core/llm.py` (`stream_chat_with_display()`) |
| **native tool calling** | The LLM returns a structured `tool_call` object in its response (OpenAI function-calling protocol). Preferred over text-based extraction. | `core/react_loop.py` |
| **text-based tool call** | Fallback: if the LLM doesn't produce native tool calls, the loop extracts tool invocations from the free text via regex (`extract_text_tool_calls()`). Fragile. | `core/react_loop.py` |
| **`create_client(step_name)`** | Factory function that reads `models.json` and returns the appropriate LLM client (`OpenRouterClient`, `OllamaClient`, or `ClaudeClient`) for a given pipeline step. | `core/llm.py` |
| **`parse_json_response()`** | Three-strategy JSON parser for LLM output: direct parse → regex extract → keyword heuristic. Used for PM review results. | `core/llm.py` |

---

## Group 4: Context / Artifact Terms

| Term | Definition | Code location |
|------|-----------|--------------|
| **`_context/<task_id>/`** | Per-task directory where all stage artifacts are stored as validated JSON files. Cleared when task reaches `done`. | `config.py` (`CONTEXT_DIR`) |
| **`ResearchContext`** | Pydantic model: research findings including relevant files, patterns, data flow, warnings, and summary. Produced by ResearchAgent, consumed by ArchitectAgent. | `dtypes.py` |
| **`ArchitectResult`** | Pydantic model: skeleton files, PLAN section, subtask proposals, and summary. Produced by ArchitectAgent, reviewed by PMAgent. | `dtypes.py` |
| **`DeveloperResult`** | Pydantic model: implemented files and summary. Produced by DevAgent, passed to TestAgent. | `dtypes.py` |
| **`ReviewResult`** | Pydantic model: `approved` (bool), `issues` (list), `overall_comment`, `feedback`, and `subtask_modifications`. Produced by PMAgent. | `dtypes.py` |
| **`CIResult`** | Pydantic model: CI outcome. `status: CIStatus` (committed / failed / commit_failed), `sha`, `commit_message`, `output`. | `dtypes.py` |
| **`TestingContext`** | Pydantic model: all files (impl + tests) + `CIResult` + summary. Persisted after CI and consumed by PM for final review. | `dtypes.py` |
| **`FeedbackContext`** | Pydantic model: list of `FeedbackEntry` records, one per rejection. Each entry records source, stage, retry count, and issues. | `dtypes.py` |
| **`FeedbackEntry`** | Single rejection record: `source` (e.g., "pm"), `stage` (e.g., "develop"), `retry` (int), `issues` (list of strings). | `dtypes.py` |
| **`FileContent`** | Pydantic model: `path` + `content` — the atomic unit of file transfer between agents. | `dtypes.py` |
| **`ReactLoopSummary`** | Compact log of a single ReAct loop execution: round count, tool sequence, files written, errors, outcome, finish summary. | `dtypes.py` |
| **`project_context(root)`** | Context manager that sets the thread-local target project root. All tool file operations resolve paths against this root. | `core/tools.py` |
| **`tool_scope()`** | Context manager that manages the per-task written-files accumulator. Rollback on failure exit: calls `_rollback_written_files()`. | `core/tools.py` |
| **`_save_typed_context()`** | Helper that serializes a Pydantic model to `_context/<task_id>/<key>.json`. | `event_loop.py` |
| **`_load_typed_context()`** | Helper that loads and validates a Pydantic model from `_context/<task_id>/<key>.json`. Returns `None` on `ValidationError`. | `event_loop.py` |

---

## Group 5: A2A / Observability Terms

| Term | Definition | Code location |
|------|-----------|--------------|
| **A2A Protocol** | Agent-to-Agent message format for inter-agent handoffs. Based on the Anthropic A2A standard. | `dtypes.py` (`A2AMessage`) |
| **A2AMessage** | Structured message with: `id`, `kind`, `from_agent`, `to_agent`, `task_id`, `summary`, `payload`, `attachments`. | `dtypes.py` |
| **A2A Gateway** | A ZMQ TCP listener (`a2a_server.py`) that receives published messages and appends them to `_a2a/messages.jsonl`. Optional. | `a2a_server.py` |
| **`_a2a/messages.jsonl`** | Append-only log of all A2A messages. Can be replayed by an inspector tool to visualize agent activity. | `a2a_server.py` |
| **run event** | A Dashboard API object tracking a single agent execution: `run_id`, agent type, status, output. Created and updated by the event loop. | `event_loop.py`, `clients/dashboard_client.py` |
| **`error.log`** | Plain-text file in `_context/<task_id>/error.log` containing the Python stack trace from the most recent task failure. | `event_loop.py` |
