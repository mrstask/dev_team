# 01 — Introduction & Goals

> **arc42 question**: *Why does this system exist? What must it achieve? Who cares about it?*

← [[00-index|Index]] | Next: [[02-constraints]] →

---

## 1.1 Purpose

**Dev Team** eliminates the human bottleneck in the software development loop. Given a task description in a dashboard, it autonomously:

1. **Researches** the target codebase for relevant patterns and files.
2. **Designs** skeleton files and a structured implementation plan.
3. **Implements** the plan by filling in the skeleton files.
4. **Tests** the result with pytest and pylint.
5. **Reviews** every stage with an AI PM agent.
6. **Commits** the code to the target project's git repository.

No human approvals are required in the default configuration. The system is project-agnostic — it reads a `root_path` from the dashboard and works on any Python project.

### The Problem It Solves

Writing software is expensive and slow when every review cycle requires a human. Dev Team moves the review, iteration, and approval loop entirely into the machine — so a task can go from "backlog" to "committed code" overnight without human intervention.

---

## 1.2 Quality Goals

These are the top architectural qualities. They drive the design decisions in [[04-solution-strategy]] and [[09-architecture-decisions]].

| Priority | Quality Goal | Scenario | Mechanism |
|----------|-------------|----------|-----------|
| 1 | **Reliability** | A rate-limited LLM or a stalled response must not lose the task | Fallback models, stall detection, retry counter, task rollback |
| 2 | **Autonomy** | The system runs overnight without human intervention | AI PM reviewer, action labels, no blocking waits |
| 3 | **Backend Agnosticism** | Swap OpenRouter → Ollama → Claude SDK without touching agent code | `models.json` + `create_client()` factory |
| 4 | **Data Integrity** | Agent output must match expected schema before being passed to the next stage | Pydantic models validated at every boundary |
| 5 | **Observability** | An operator can reconstruct what happened during a task execution | A2A message log, `_context/<task_id>/` artifacts, `error.log` |

---

## 1.3 Stakeholders

| Role | Concern | How this doc helps |
|------|---------|-------------------|
| **System owner / operator** | Is the pipeline running? Did the task succeed? | [[06-runtime-view]], [[11-risks-technical-debt]] |
| **Developer extending dev_team** | Where do I add a new agent step? How are tools scoped? | [[05-building-blocks]], [[08-crosscutting-concepts]] |
| **Developer of the target project** | What code will be committed? Will tests pass? | [[06-runtime-view]] (CI flow), [[09-architecture-decisions]] ADR-011 |
| **LLM / infra maintainer** | What models are in use? How do I swap them? | [[07-deployment-view]], [[09-architecture-decisions]] ADR-009 |

---

## 1.4 Scope Summary

```
┌─────────────────────────────────────────┐
│              Dev Team                   │
│                                         │
│  Research → Architect → Develop →       │
│  Test → PM Review → Commit              │
│                                         │
│  Driven by: Dashboard API task queue    │
│  Target:    Any Python project          │
│  Output:    Committed, tested code      │
└─────────────────────────────────────────┘
```

See [[03-system-context]] for the full C4 System Context diagram showing all external systems.
