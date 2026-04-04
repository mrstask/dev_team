---
description: Implement an approved plan from plans/ phase by phase with verification
---

# Implement Plan

You are tasked with implementing an approved plan from `plans/`. Plans contain phases with specific file changes and success criteria. Your job is to follow the plan's intent faithfully, verify each phase before proceeding, and pause for human confirmation at phase boundaries.

## Getting Started

When given a plan path:
1. Read the plan completely — check for any existing `- [x]` checkmarks
2. Read `CLAUDE.md` from the project root
3. Read every file mentioned in the plan that you'll be editing — **fully**, no limit/offset
4. Think through how the pieces fit together before writing any code
5. Create a todo list to track your progress through phases
6. Begin with the first unchecked phase

If no plan path is provided, ask:
```
Please provide the path to the plan file, e.g.:
  /implement_plan plans/2025-04-04-article-feed-pagination.md
```

## Codebase Layout

```
ROOT/
  backend/          ← Python FastAPI + SQLAlchemy (primary implementation target)
    app/
      models/       ← SQLAlchemy 2.x models (Mapped[], mapped_column)
      routers/      ← FastAPI routers (async functions)
      services/     ← business logic
      repositories/ ← DB access layer
    tests/          ← pytest tests
  frontend/         ← React/TypeScript
  alembic/          ← DB migrations
  CLAUDE.md         ← canonical file index
```

## Implementation Philosophy

The plan was carefully designed, but reality can be messy. Your job is to:
- Follow the plan's **intent**, adapting to what you actually find in the code
- Implement each phase fully before moving to the next
- Check off items in the plan as you complete them using the Edit tool
- Communicate clearly when something doesn't match

**If the codebase has diverged from the plan**, stop and explain:
```
Issue in Phase [N]:
  Expected: [what the plan says]
  Found:    [actual situation]
  Why this matters: [impact on the phase]

How should I proceed?
```

Do not silently work around mismatches — surface them immediately.

## Phase Execution

For each phase:

1. **Re-read the files you'll edit** — confirm current state before making changes
2. **Implement all changes** described in the phase
3. **Run automated verification**:
   ```bash
   pytest backend/tests/ --tb=short -q          # tests — only failures shown
   pylint backend/app/ --output-format=text --score=no  # lint
   ```
4. **Fix any failures** before moving on — don't proceed with broken tests
5. **Check off completed items** in the plan file using the Edit tool:
   - Change `- [ ]` to `- [x]` for each completed automated criterion
6. **Pause for manual verification**:

```
Phase [N] complete — ready for manual verification.

Automated checks passed:
- [x] pytest backend/tests/ --tb=short -q
- [x] pylint backend/app/

Please perform the manual verification steps from the plan:
- [ ] [Manual step 1 from plan]
- [ ] [Manual step 2 from plan]

Let me know when manual testing is complete and I'll proceed to Phase [N+1].
```

Do **not** check off manual verification items until the user confirms them.

If the user says "proceed through all phases", skip the pause between phases and only pause at the very end.

## Coding Standards

Follow these conventions — they match the existing codebase:

**Python / Backend:**
- All DB and HTTP calls must be `async`
- SQLAlchemy 2.x: `Mapped[T]`, `mapped_column()`, `relationship()`
- Pydantic v2: `model_config = ConfigDict(...)`, not `class Config`
- FastAPI routers: `APIRouter`, `Depends()`, typed request/response bodies
- Import order: stdlib → third-party → local (`from app.models...`)
- Never use synchronous I/O in async contexts

**File paths** (when writing files):
- Always relative to project root: `backend/app/models/foo.py` ✓
- Never include the project folder name: `habr-agentic/backend/...` ✗

**Tests:**
- Mirror the structure of `backend/tests/test_[module].py`
- Use `pytest-asyncio` for async tests
- `conftest.py` handles `sys.path` — do not duplicate it

## Verification Commands

```bash
# Run all tests, failures only
pytest backend/tests/ --tb=short -q

# Run a specific test file
pytest backend/tests/test_articles.py --tb=short -q

# Lint
pylint backend/app/ --output-format=text --score=no

# Lint a single module
pylint backend/app/routers/articles.py --output-format=text --score=no

# Check for import errors
python -c "from backend.app.main import app; print('OK')"

# Run DB migration (if the phase includes alembic changes)
alembic upgrade head
```

## Resuming Work

If the plan already has checkmarks:
- Trust that completed phases are done
- Pick up from the first unchecked item
- Verify previous work only if something seems off (e.g. tests are failing)

## Dev Team Pipeline Integration

If the task was created via the dev_team pipeline and you're implementing manually (overriding the autonomous agents):

```bash
python main.py board                          # see current task states
python main.py review <task_id>               # inspect agent output
python main.py approve <task_id>              # continue to next stage
python main.py reject <task_id> "feedback"   # retry with notes
```

For tasks where the autonomous pipeline should run the implementation instead:
- Use `python main.py kick <task_id>` to start the architect stage
- The pipeline will run: architect → develop → test → done
- Monitor with `python main.py board`

## If You Get Stuck

When something isn't working:
1. Re-read the relevant source files — the plan may reference an older version
2. Check if the codebase has evolved since the plan was written (`git log --oneline -10`)
3. Present the mismatch clearly and ask for guidance — don't silently rework the plan

Use sub-agents sparingly — only for targeted debugging or exploring a specific unfamiliar area.
