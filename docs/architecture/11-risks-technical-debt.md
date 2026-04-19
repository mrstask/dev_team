# 11 — Risks & Technical Debt

> **arc42 question**: *What could go wrong? What shortcuts were taken that may need revisiting?*

← [[10-quality-requirements]] | Next: [[12-glossary]] →

---

## 11.1 Risk Register

Risks are rated on a 1–3 scale: **L** (likelihood) and **I** (impact). Priority = L × I.

| ID | Risk | L | I | Priority | Mitigation |
|----|-----|---|---|----------|-----------|
| **RISK-01** | Both primary and fallback model are rate-limited simultaneously | 2 | 2 | 4 | Sleep-and-retry loop in `stream_chat_with_display()`; configure a second fallback with a different provider tier |
| **RISK-02** | Dashboard API is down for an extended period | 1 | 3 | 3 | Event loop retries every 10s; operator restarts service |
| **RISK-03** | LLM produces syntactically valid but semantically wrong code that passes tests | 2 | 3 | 6 | Enable PM human gate for testing stage; add integration tests to target project |
| **RISK-04** | Target project `.venv/` is missing or corrupt | 2 | 2 | 4 | Pre-flight check not implemented; CI fails with `CIResult(status: "failed")` |
| **RISK-05** | Agent writes files outside the target project root | 1 | 3 | 3 | `project_context()` restricts paths via `get_project_root()`; however absolute paths in LLM output can bypass this |
| **RISK-06** | Max retries (5) reached without resolution | 2 | 2 | 4 | Task marked `failed + error:max-retries`; operator must manually `kick` or improve the task description |
| **RISK-07** | LLM hallucinates tool call syntax; text extraction fallback misparses it | 2 | 2 | 4 | Fallback is fragile; use models with strong native tool calling support |
| **RISK-08** | Context artifacts accumulate on disk (never cleaned for failed tasks) | 2 | 1 | 2 | Auto-cleanup on `done`; failed tasks leave artifacts; operator must manually prune `_context/` |
| **RISK-09** | A2A message log grows unbounded | 1 | 1 | 1 | `_a2a/messages.jsonl` is append-only; rotate or truncate periodically |
| **RISK-10** | PM approves code that breaks existing functionality | 2 | 3 | 6 | Full test suite in target project is the real safety net; PM quality depends on model capability |

---

## 11.2 Known Failure Modes

| Failure mode | Symptom | Recovery |
|-------------|---------|---------|
| LLM silent for >180s per request | `LLMStallError` in logs, task eventually marked `failed` | Retry; check LLM provider status |
| Agent produces no tool calls for 100 rounds | "max rounds" log message, loop returns `None` | Rollback + retry; consider stronger model |
| `finish()` never called | Agent writes files but task never progresses | Rollback via `_rollback_written_files()`; retry |
| Dashboard PATCH wipes fields | Fields appear as `null` in dashboard | Already mitigated: `update_task()` fetches full state before PATCH |
| Git commit fails (no git or wrong branch) | `CIResult(status: "commit_failed")` | Check git config in target project |
| `pytest` cannot be found in `.venv/` | `FileNotFoundError` in `_find_venv_bin()` | Install pytest in target project venv |
| Research produces empty context | Architect gets no context; produces generic skeleton | Auto-extract fallback in `ResearchAgent.run()` using read_file call history |
| Subtask creation fails (Dashboard error) | Parent task stalls at architect stage | Check Dashboard API health; retry with `kick` |

---

## 11.3 Hard Dependencies

| Dependency | Impact if unavailable | No fallback? |
|-----------|----------------------|-------------|
| Dashboard API (`localhost:8000`) | Complete — no tasks can be queued or tracked | No fallback |
| At least one LLM backend (OpenRouter or Ollama) | Complete — no agent can run | No fallback if both down |
| Python 3.11+ | Build-time — modern type hints won't parse | No fallback |
| `git` CLI in PATH | CI commits fail with `commit_failed` status | Tests still run; code is written |
| `pytest` in target project `.venv/` | CI fails to run; `CIResult(status: "failed")` | No fallback |

---

## 11.4 Technical Debt

These are known shortcuts or incomplete implementations that may need to be addressed as the system scales.

| ID | Area | Debt description | Risk if unaddressed |
|----|------|-----------------|-------------------|
| **TD-01** | `config.py` | `ROOT` fallback path is hardcoded to a specific project (`habr-agentic`). If `get_project_root()` fails, this path is used silently. | Wrong project root used for file operations |
| **TD-02** | `core/react_loop.py` | Text-based tool call extraction (`extract_text_tool_calls()`) is a fragile heuristic — regex pattern matching on LLM prose output. | Silent parse failures with weaker models; wrong tool name or args extracted |
| **TD-03** | `prompts/` | System prompts are hardcoded Python string constants. No versioning, no A/B testing, no easy diff of prompt changes. | Prompt regressions go undetected; hard to improve prompts systematically |
| **TD-04** | `agents/tester.py` | Pylint violations are advisory only and not stored in `CIResult`. They are printed but not persisted. | Quality issues accumulate silently in target project |
| **TD-05** | `event_loop.py` | Context artifacts for `failed` tasks are not cleaned up. `_context/<id>/` stays on disk indefinitely. | Disk accumulation on high-failure pipelines |
| **TD-06** | `clients/dashboard_client.py` | No authentication on the Dashboard API. Assumes it runs on localhost behind a firewall. | Security risk if exposed to a network |
| **TD-07** | `core/tools.py` | `write_file()` does not sanitize absolute paths that start with `/`. An LLM could write outside the project root. | File system pollution if LLM produces unexpected absolute paths |
| **TD-08** | `agents/pm.py` | Heuristic JSON parsing (keyword detection) masks model quality issues — a model that says "this looks approved" passes. | Silent approval of bad output with very weak models |

---

> See [[09-architecture-decisions]] for the rationale behind choices that introduced some of this debt.
> See [[10-quality-requirements]] for the quality scenarios that mitigate these risks.
