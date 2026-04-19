# 08 — Crosscutting Concepts

> **arc42 question**: *What patterns, principles, and solutions apply across the whole system — not just one component?*

← [[07-deployment-view]] | Next: [[09-architecture-decisions]] →

---

Crosscutting concepts are the "connective tissue" of the architecture. They don't belong to a single component — they govern how all components behave. If you understand these, you understand why the code is structured the way it is.

---

## 8.1 The ReAct Loop Pattern

**What it is**: ReAct (Reasoning + Acting) is an LLM interaction pattern where the model alternates between reasoning steps and tool calls. In Dev Team, every agent that needs to interact with the filesystem uses this pattern.

**How it works** (`core/react_loop.py`):
```
[System prompt + tool specs]
        │
        ▼
1. Send message history to LLM (streaming)
        │
        ▼
2. LLM responds: either tool call(s) or free text
        │
   ┌────┴─────────┐
   │              │
Tool calls    No tool call
   │              │
   ▼              ▼
3. Execute    Extract text-based
   tools      tool calls (fallback)
   │              │
   └────┬─────────┘
        │
        ▼
4. Append tool results to history
        │
        ▼
5. Repeat (max 100 rounds) until finish() or submit_research()
```

**Key property**: All five agents share *the same loop*. Agent differentiation comes entirely from system prompt and tool spec subset — the loop itself is backend-agnostic.

**Stall protection**: If the LLM produces no tokens for 180 seconds (`LLM_STALL_TIMEOUT`), a `LLMStallError` is raised. The loop retries up to 3 times (`LLM_STALL_MAX_RETRIES`), then marks the task failed.

**Text tool call fallback**: Some weaker models don't use native function calling. The loop falls back to regex extraction of tool calls from free text (`extract_text_tool_calls()` in `core/react_loop.py`). This is fragile but ensures partial compatibility.

---

## 8.2 Thread-Local State: `project_context()` and `tool_scope()`

Dev Team is single-threaded but the context managers use `threading.local()` for future-proofing and to keep state cleanly scoped.

### `project_context(root)` — `core/tools.py`

Sets the target project root path for the current task. All tool functions (`read_file`, `write_file`, etc.) resolve relative paths against this root.

```python
with project_context("/path/to/target_project"):
    # All tool calls inside are scoped to this root
    run_react_loop(agent)
# root cleared on exit
```

This is called in `event_loop.py` before dispatching each task. The root is fetched from the dashboard via `DashboardClient.get_project_root(project_id)`.

### `tool_scope()` — `core/tools.py`

Manages the per-task written-files accumulator. Cleared on both normal and error exit.

```python
with tool_scope():
    result = run_react_loop(agent)
    # result.files = files accumulated via write_file() calls
# On normal exit: files returned
# On failure exit: _rollback_written_files() deletes them from disk
```

**Why two separate context managers?** `project_context` is about *where* files live; `tool_scope` is about *tracking* which files were written. They can be nested in different orders without conflict.

---

## 8.3 Pydantic Validation at Every Boundary

Every inter-agent data transfer is validated by a Pydantic model. The validation happens at two points:

1. **Save** (agent → context file): `model.model_dump()` produces the JSON written to `_context/<task_id>/`.
2. **Load** (context file → next agent): `Model.model_validate(raw)` in `event_loop.py`. If validation fails, the error is logged and `None` is returned — the task is reset to `action:todo` rather than crashing.

**Frozen result models**: `ArchitectResult`, `DeveloperResult`, `ReviewResult`, `CIResult` all use `ConfigDict(frozen=True)`. This prevents accidental mutation after construction.

**Literal types**: `CIStatus = Literal["committed", "failed", "commit_failed"]` means Pydantic will reject any string that isn't one of those three values. This catches LLM hallucinations that produce invalid status strings.

---

## 8.4 Structured Feedback Loop

Feedback is not appended as free text to the task description — it is stored as typed records in `feedback.json`.

```python
# dtypes.py
class FeedbackEntry(BaseModel):
    source: str          # e.g. "pm", "human"
    stage: str           # e.g. "develop", "testing"
    retry: int           # retry count when this feedback was created
    issues: list[str]    # specific issues raised by the reviewer
```

When the developer retries, the prompt includes all prior feedback entries, ordered by retry count. This means:
- The developer sees *what* was wrong, not just "rejected".
- Each entry is indexed, so the developer can tell which issues are from which retry.
- No context is lost across retries — all accumulated feedback is passed in.

---

## 8.5 A2A Observability Protocol

After each agent handoff, Dev Team publishes an `A2AMessage` to the gateway. This is non-blocking — failures are swallowed silently.

```python
# dtypes.py
class A2AMessage(BaseModel):
    id: str
    kind: Literal["request", "handoff", "review", "decision", "system"]
    from_agent: str      # e.g. "architect:design"
    to_agent: str        # e.g. "developer:implement"
    task_id: str
    summary: str
    payload: dict        # stage-specific data
    attachments: list    # file contents, if any
```

Messages are appended to `_a2a/messages.jsonl` and can be replayed in an inspector UI. The protocol follows the [Anthropic Agent-to-Agent (A2A) standard](https://anthropic.com).

---

## 8.6 Error Handling Hierarchy

Errors are handled at multiple levels, from innermost to outermost:

| Level | Mechanism | Recovery |
|-------|-----------|---------|
| **LLM stall** (no tokens for 180s) | `LLMStallError` in `core/llm.py` | Retry up to 3 times, then raise |
| **LLM rate limit** (HTTP 429) | `LLMRateLimitError` in `core/llm.py` | Switch to fallback model, retry |
| **ReAct max rounds** (100 reached) | Loop exits returning `None` | `_rollback_written_files()`, task increments retry |
| **Agent no-output** (loop returns None) | Checked in `event_loop.py` | Rollback + task retry with `action:todo` |
| **Task max retries** (5 exceeded) | `event_loop.py` | Task marked `failed + error:max-retries` |
| **Unhandled exception** | `try/except` in `event_loop.py` | Stack trace saved to `error.log`, task marked `failed + error:exception` |
| **Dashboard unreachable** | Connection error in poll loop | Log warning, sleep 10s, retry next poll |

---

## 8.7 JSON Parsing Strategy for LLM Output

PM agent responses must be parsed as JSON (`ReviewResult`). LLMs don't always produce valid JSON, so `parse_json_response()` in `core/llm.py` tries three strategies in order:

1. **Direct parse**: `json.loads(text)` — works when the model produces clean JSON.
2. **Regex extract**: finds the first `{...}` block in the text and parses it — works when the model wraps JSON in prose.
3. **Heuristic**: scans for keywords `"approved"`, `"lgtm"`, `"no issues"` (→ approved=True) or `"rejected"`, `"issues"` (→ approved=False) — last resort for very weak models.

---

> See [[09-architecture-decisions]] for the rationale behind each of these patterns.
> See [[10-quality-requirements]] for how these patterns map to quality goals.
