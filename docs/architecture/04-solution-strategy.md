# 04 — Solution Strategy

> **arc42 question**: *What are the fundamental, load-bearing decisions that shape the whole architecture?*

← [[03-system-context]] | Next: [[05-building-blocks]] →

---

Solution strategy is a summary of the *most important* architectural choices. Each choice here has a corresponding ADR in [[09-architecture-decisions]] with full context and trade-offs.

---

## 4.1 ACE Workflow: Research → Plan → Implement

The pipeline follows the **Advanced Context Engineering (ACE)** pattern to maximize LLM output quality:

```
backlog
  │
  ▼
[Research]   ←── ResearchAgent reads the target codebase (read-only ReAct loop)
  │               Produces: ResearchContext (files, patterns, data flow, warnings)
  ▼
[Architect]  ←── ArchitectAgent consumes research artifact
  │               Produces: skeleton files + PLAN section + subtask proposals
  ▼
[Develop]    ←── DevAgent fills in skeleton TODOs
  │               Receives: skeleton files + structured feedback (on retry)
  ▼
[Test + CI]  ←── TestAgent generates tests, runs pytest + pylint, commits
  │               Iterative fix loop: up to 3 rounds if tests fail
  ▼
[PM Review]  ←── PMAgent reviews at architect, develop, and testing stages
  │               Approved → next stage | Rejected → retry with feedback
  ▼
done
```

**Why ACE?** Giving each agent a focused context — rather than one monolithic prompt — dramatically improves output quality. The researcher knows what to look for; the architect knows the codebase landscape; the developer has a clear skeleton to fill in.

---

## 4.2 Polling Over Event-Driven

Dev Team **polls** the Dashboard API every 10 seconds rather than subscribing to events. See ADR-001 in [[09-architecture-decisions]].

- **Benefit**: Simpler coordination (no message broker, no webhook infrastructure), easier error recovery (re-poll on failure), stateless loop.
- **Trade-off**: Up to 10-second latency between task creation and pickup. Acceptable for batch processing.
- **Config**: `EVENT_LOOP_POLL_INTERVAL` in `config.py`.

---

## 4.3 Backend-Agnostic LLM via `models.json`

Every pipeline step's LLM backend is fully configurable in `models.json` — no LLM-specific logic lives in agent classes. The factory function `create_client(step_name)` in `core/llm.py` reads this config at runtime.

```json
{
  "steps": {
    "researcher": { "backend": "openrouter", "model": "qwen/qwen3-30b-a3b:free", "fallback": {...} },
    "architect":  { "backend": "openrouter", "model": "qwen/qwen3-30b-a3b:free", "fallback": {...} }
  }
}
```

Supported backends: `"openrouter"` | `"ollama"` | `"claude-code"`. Switching a step requires only editing `models.json`. See ADR-009.

---

## 4.4 Pydantic as the Data Contract Layer

All inter-agent data flows through **Pydantic-validated models** defined in `dtypes.py`. Key examples:

| Agent output | Model | Validation point |
|---|---|---|
| ResearchAgent → ArchitectAgent | `ResearchContext` | Loaded via `model_validate()` in `event_loop.py` |
| ArchitectAgent → PMAgent | `ArchitectResult` | Validated on both save and load |
| PMAgent decision | `ReviewResult` | Three-strategy JSON parsing before validation |
| TestAgent CI run | `CIResult` | `CIStatus = Literal["committed", "failed", "commit_failed"]` |

**Why?** Schema drift between pipeline stages causes silent bugs. Pydantic catches malformed agent output immediately with a clear validation error, rather than a cryptic `KeyError` three steps later.

---

## 4.5 Tool Isolation via `tool_scope()`

Every agent's file-write activity is tracked in a **thread-local accumulator** scoped to the current task. When the agent fails (no `finish()` call), all written files are deleted from disk.

```python
# core/tools.py — simplified
with tool_scope():           # clears _written_files and _written_paths
    run_react_loop(agent)    # agent calls write_file() during loop
# on normal exit:  files returned via finish()
# on failure exit: _rollback_written_files() deletes them
```

**Why?** Without this, a failed architect that partially wrote skeleton files would confuse the next retry attempt. The rollback ensures each retry starts from a clean slate. See ADR-003 and ADR-006.

---

## 4.6 Shared ReAct Loop Engine

All five agents (Research, Architect, Developer, Tester, PM-in-review) share a single `run_react_loop()` function in `core/react_loop.py`. The loop:

1. Sends the current message history to the LLM (streaming).
2. Parses the response for tool calls (native protocol first, text-based fallback second).
3. Executes the tool and appends the result to message history.
4. Repeats until `finish()` or `submit_research()` is called, or max rounds (100) is reached.

**Why one shared loop?** Unified error handling (stall detection, rate-limit fallback, round limits), unified streaming display, and no code duplication across agents. Agents are differentiated by their system prompt and tool spec subset — not by loop logic. See ADR-004.

---

## 4.7 Structured Feedback for Retries

When PM rejects a developer's output, the rejection is stored as a `FeedbackEntry` in `_context/<task_id>/feedback.json` — not appended as free text to the task description. On the next retry, the developer's prompt includes:

```
[Retry 1 feedback — source: pm, stage: develop]
Issues:
- The function signature does not match the interface contract
- Missing error handling for None inputs
```

**Why?** Structured feedback lets the developer address specific issues without being confused by accumulated free-text history. Each entry is indexed by retry count, so the developer sees the most recent feedback prominently. See ADR-007.

---

> Full rationale for each decision: [[09-architecture-decisions]]
> How these strategies manifest in code: [[05-building-blocks]], [[08-crosscutting-concepts]]
