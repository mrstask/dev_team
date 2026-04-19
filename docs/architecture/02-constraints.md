# 02 — Architecture Constraints

> **arc42 question**: *What are the non-negotiable limits — technical, organizational, or legal — that the architecture must work within?*

← [[01-introduction-goals]] | Next: [[03-system-context]] →

---

Constraints differ from design decisions: you didn't choose them, they were given to you. They narrow the solution space and must be respected in every design decision in [[09-architecture-decisions]].

---

## 2.1 Technical Constraints

| ID | Constraint | Reason / Source | Impact on Architecture |
|----|-----------|-----------------|----------------------|
| TC-01 | **Python 3.11+** | Type hint syntax (`X \| Y`, `match`, `TypeAlias`) used throughout | No Python 2 / early 3.x compatibility |
| TC-02 | **Dashboard API must be reachable at `http://localhost:8000/api`** | Task queue, status updates, context — all go through this API | No offline mode; event loop dies if API is down |
| TC-03 | **At least one LLM backend must be configured** | Agents cannot run without a model | `models.json` must have valid `backend` + `model` for all 5 steps |
| TC-04 | **Target project must have a Python `.venv/`** | `pytest` and `pylint` are resolved from the target project's venv via `_find_venv_bin()` | CI step hard-fails if venv is missing |
| TC-05 | **Target project must be a git repository** | `TestAgent.run_ci()` commits via `git` subprocess | `commit_failed` CI status if git is absent |
| TC-06 | **Single-threaded execution** | Event loop is synchronous (`asyncio` is not used); one task at a time | No parallel agent execution; throughput scales by running multiple instances |
| TC-07 | **HTTPX for HTTP clients** | All REST calls (Dashboard, OpenRouter, Ollama) use HTTPX with streaming | No `requests` or `aiohttp` |
| TC-08 | **Pydantic v2** | All data contracts use `model_validate()`, `ConfigDict(frozen=True)`, `Field(description=...)` | Code uses v2 API; v1 aliases will not work |

---

## 2.2 Organizational Constraints

| ID | Constraint | Reason / Source | Impact on Architecture |
|----|-----------|-----------------|----------------------|
| OC-01 | **No human in the loop by default** | Core design goal — fully autonomous pipeline | PM agent must be able to approve/reject without human input |
| OC-02 | **Human gates are opt-in** | Operator may want to review architect output before developers start | `HUMAN_GATES` dict in `config.py`; `action:await-human` label pauses pipeline |
| OC-03 | **API keys stored in `.env`, never committed** | Secret hygiene | `config.py` loads via `python-dotenv`; `.gitignore` must exclude `.env` |
| OC-04 | **`models.json` is the only place to configure LLM backends** | Keeps LLM choice out of agent code | Changing a model requires only editing `models.json` — no code changes |
| OC-05 | **Dashboard is the source of truth for task state** | Enables external monitoring and multi-client coordination | Dev Team never holds state in memory across poll cycles; always re-fetches from dashboard |

---

## 2.3 Conventions

| Convention | Details |
|-----------|---------|
| **Immutable result models** | `ArchitectResult`, `DeveloperResult`, `ReviewResult`, `CIResult` use `ConfigDict(frozen=True)` |
| **No bare list/dict defaults** | Always `Field(default_factory=list)` — never `= []` |
| **Literal types over plain str** | `CIStatus`, `Backend`, `TaskStatusName`, `TaskPriorityName` use `Literal[...]` |
| **Field descriptions** | All Pydantic fields use `Field(description=...)` for schema introspection |
| **Tool specs as OpenAI function dicts** | Tool definitions in `core/tools.py` follow the OpenAI function-calling schema |

---

> See [[09-architecture-decisions]] for how these constraints shaped specific design choices — e.g., ADR-001 (polling over event-driven) is a direct consequence of OC-05.
