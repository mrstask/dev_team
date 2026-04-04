---
description: Create a detailed implementation plan through interactive research and iteration
model: opus
---

# Create Plan

You are tasked with creating detailed, actionable implementation plans through an interactive, iterative process. Be skeptical, thorough, and work collaboratively with the user to produce a plan with zero open questions before finalising.

## Codebase Layout

```
ROOT/                   ← target project root (see dev_team/config.py)
  backend/              ← Python FastAPI + SQLAlchemy
  frontend/             ← React/TypeScript
  alembic/              ← DB migrations
  CLAUDE.md             ← canonical file index (read this first)

dev_team/               ← this repo
  plans/                ← research docs and implementation plans
    research/           ← output of /research_codebase
  agents/               ← architect, developer, pm, tester
  prompts/              ← agent system/user prompts
  event_loop.py         ← pipeline state machine
```

## Initial Response

When invoked without parameters:
```
I'll help you create an implementation plan. Please provide:
1. A description of the task or feature (or paste a task from the dashboard)
2. Any relevant constraints, patterns to follow, or prior research

Tip: If you already have a research document, reference it:
  /create_plan plans/research/2025-04-04-article-feed-data-flow.md
```

Then wait for input.

If a file path is provided as a parameter, read it immediately and begin.

## Process

### Step 1: Context Gathering

1. **Read all mentioned files FULLY** — no limit/offset parameters, always complete files
2. **Read `CLAUDE.md`** from the project root for the canonical file index
3. **Spawn parallel research sub-agents** before asking the user questions:

   - **File locator** — find all files relevant to the task
   - **Implementation analyser** — understand how the current code in the affected area works
   - **Pattern finder** — find similar existing features to model after
   - **Test pattern agent** — find how existing tests are structured in `backend/tests/`

   Each agent uses only read tools and returns `file:line` references.

4. **Read all files identified by research** — bring them into main context
5. **Present informed understanding + focused questions**:
   ```
   Based on the task and my research, I understand we need to [accurate summary].

   I found:
   - [Current implementation detail — file:line]
   - [Relevant pattern or constraint]
   - [Potential complexity]

   Questions my research couldn't answer:
   - [Specific question requiring human judgement]
   - [Design preference with implementation impact]
   ```

   Only ask questions you genuinely cannot answer through code investigation.

### Step 2: Research & Discovery

1. If the user corrects any misunderstanding — spawn new sub-agents to verify before proceeding
2. Spawn additional parallel research for deeper investigation as needed
3. Present design options with trade-offs:
   ```
   Design Options:
   1. [Option A] — pros/cons, files affected
   2. [Option B] — pros/cons, files affected

   Which fits best?
   ```

### Step 3: Plan Structure

Present the phase outline and get approval before writing details:
```
Proposed phases:
1. [Phase name] — [what it accomplishes, ~N files]
2. [Phase name] — [what it accomplishes, ~N files]
3. [Phase name] — [what it accomplishes]

Does this phasing make sense?
```

### Step 4: Write the Plan

Save to: `plans/YYYY-MM-DD-{kebab-description}.md`

Examples:
- `plans/2025-04-04-article-feed-pagination.md`
- `plans/2025-04-04-add-auth-middleware.md`

Use this template:

````markdown
# [Feature/Task Name] Implementation Plan

## Overview
[1–2 sentences: what we're building and why]

## Current State
[What exists today, what's missing, key constraints — with file:line refs]

## Desired End State
[Concrete description of what "done" looks like and how to verify it]

## What We Are NOT Doing
[Explicit out-of-scope items to prevent scope creep]

## Key Design Decisions
- [Decision 1 and rationale]
- [Decision 2 and rationale]

---

## Phase 1: [Descriptive Name]

### Overview
[What this phase accomplishes and why it comes first]

### Changes

#### `backend/app/models/foo.py` — [purpose]
```python
# specific code to add / change
```

#### `backend/app/routers/foo.py` — [purpose]
```python
# specific code
```

### Success Criteria

#### Automated
- [ ] Tests pass: `pytest backend/tests/ --tb=short -q`
- [ ] Lint clean: `pylint backend/app/`
- [ ] No import errors: `python -c "from backend.app.main import app"`

#### Manual
- [ ] [Specific UI or API behaviour to verify by hand]
- [ ] [Edge case to test manually]

**Pause here** — confirm manual verification before proceeding to Phase 2.

---

## Phase 2: [Descriptive Name]

[Same structure...]

---

## Testing Strategy

### Unit Tests (`backend/tests/`)
- [What to test, key edge cases]
- Follow pattern from `backend/tests/test_[similar_module].py`

### Integration Tests
- [End-to-end scenarios]

### Manual Testing Steps
1. [Step-by-step verification]

## Migration Notes
[If DB schema changes: alembic revision steps, rollback plan]

## References
- Research: `plans/research/[relevant].md`
- Similar implementation: `[file:line]`
- Task description: [paste or reference]
````

### Step 5: Review and Iterate

Present the draft location and ask:
```
Draft plan saved at: plans/YYYY-MM-DD-{slug}.md

Please review:
- Are the phases properly scoped?
- Are the success criteria specific enough?
- Any missing edge cases?
- Anything out of scope that crept in?
```

Iterate until the user is satisfied. Every open question must be resolved before the plan is final.

## Guidelines

**Be Skeptical**: Question vague requirements. Ask "why" and "what about X". Don't assume — verify with code.

**Be Interactive**: Don't write the full plan in one shot. Get buy-in at each step.

**Be Thorough**: Include specific `file:line` references throughout. Write measurable success criteria with clear automated vs manual split.

**No Open Questions in Final Plan**: If something is unclear, stop and resolve it. The plan must be fully actionable.

**Success Criteria Format**: Always split into Automated (runnable commands) and Manual (human verification). Automated checks must be exact commands:
```markdown
#### Automated
- [ ] `pytest backend/tests/test_articles.py --tb=short -q`
- [ ] `pylint backend/app/routers/articles.py`

#### Manual
- [ ] Pagination controls appear and navigate correctly in the UI
```

## Dev Team Pipeline Context

When the plan involves creating new tasks for the autonomous pipeline:

- Architect stage → `python main.py kick <task_id>` to start
- Check status → `python main.py board`
- Review output → `python main.py review <task_id>`
- Approve/reject → `python main.py approve <task_id>` / `python main.py reject <task_id> "feedback"`

The plan's phases map naturally to subtasks that the architect will propose. Design phases at the subtask granularity (50–300 LOC each).
