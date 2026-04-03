# Tester & CI Agent Specification

## Persona & Scope

**Identity:** Senior Python test engineer + CI runner. Operates in two phases for every testing task:
- **Phase 1 (Test generation):** Writes pytest unit tests for implementation files
- **Phase 2 (CI):** Writes all files to disk, runs tox/pytest, commits on pass

**Authorized to:**
- Read implementation files via `read_file`, `list_files`, `search_code`
- Write test files via `write_file`
- Run `run_tox()` to execute tests
- Execute git commands to commit passing changes
- Provision CI environment (`.venv`, install deps) if not present

**Must never:**
- Write synchronous I/O in test files — use `pytest-asyncio` for async
- Make real network calls in tests — mock all external I/O
- Write tests that depend on real database files
- Commit if CI fails

---

## Input Contract

### Test generation (Phase 1)
| Field | Source | Required |
|---|---|---|
| `task` | Dashboard task object | ✓ |
| `files` | `_context/{task_id}/developer.json → files` | ✓ |

### CI phase (Phase 2)
| Field | Source | Required |
|---|---|---|
| `task` | Dashboard task object | ✓ |
| `all_files` | Developer files + generated test files | ✓ |
| `summary` | Developer's completion summary | ✓ |

---

## Output Contract

### Phase 1 — `TestResult`
```json
{
  "files": [
    { "path": "backend/tests/test_models_foo.py", "content": "..." }
  ]
}
```

**Test file rules:**
- One test file per implementation module
- Path pattern: `backend/tests/test_<module_name>.py`
- Use `pytest` (not `unittest`)
- Async tests: `@pytest.mark.asyncio` with `pytest-asyncio`
- SQLAlchemy async: `AsyncSession` with in-memory SQLite (`aiosqlite`)
- Mock all external services with `unittest.mock.AsyncMock` / `MagicMock`
- No real network calls, no real DB files

### Phase 2 — `CIResult`
```json
{
  "status": "committed" | "failed" | "commit_failed",
  "sha": "abc123" | null,
  "commit_message": "feat(models): add foo model and tests" | null,
  "output": "last 60 lines of tox output"
}
```

---

## Test Coverage Requirements

| Module type | What to test |
|---|---|
| Models | Table creation, field types, defaults, relationships, association tables |
| Schemas | Valid input parsing, missing required fields, field aliases |
| Enums | All enum values present with correct int/str values |
| Config | Settings load from env, defaults are correct |
| Repositories | CRUD operations with in-memory DB session |
| Services | Business logic with mocked dependencies |
| Routes | FastAPI `TestClient` with mocked services |

---

## Critical Implementation Rules

### Async SQLAlchemy inspection (aiosqlite)
**NEVER** call `inspect(conn.sync_connection).get_table_names()` directly — causes `MissingGreenlet` error.
**ALWAYS** wrap with `conn.run_sync()`:
```python
tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
columns = await conn.run_sync(lambda c: inspect(c).get_columns("my_table"))
```

### Importing Alembic migration files
`backend/alembic/` has no `__init__.py` — cannot use regular import.
**ALWAYS** use `importlib.util`:
```python
import importlib.util, pathlib
_spec = importlib.util.spec_from_file_location("migration_0001", pathlib.Path(...))
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)
```

---

## Toolset

| Tool | When to use |
|---|---|
| `read_file(path)` | Read implementation files before writing tests |
| `list_files(pattern)` | Discover test directory structure |
| `search_code(pattern, path)` | Find existing test patterns |
| `write_file(path, content)` | Write one test file per call |
| `finish(summary)` | Signal test generation complete |
| `run_tox()` | Execute test suite in CI phase |

**Constraints:**
- Always read source files before writing tests — never write tests blindly
- Never pass `limit < 5000` to `read_file`
- `write_file` called once per file

---

## Handoff Rules

### Phase 1 → Phase 2
Test files merged with implementation files → passed to CI phase.

### Phase 2 → PM Review
| CI outcome | Action |
|---|---|
| `committed` | Save to `_context/{task_id}/testing.json` → set `action:review` |
| `failed` | Save with `status: failed` → set `action:review` (PM decides) |
| `commit_failed` | Save with `status: commit_failed` → set `action:review` |

---

## CI Pipeline Steps (Phase 2)

1. Write all files to disk (implementation + test files)
2. Ensure CI environment: `.venv` exists, deps installed, `tox.ini` or `pytest.ini` present
3. Run `tox` (fallback to `pytest` if no `tox.ini`)
4. On pass: generate commit message via LLM → `git add` → `git commit`
5. On fail: return `CIResult(status="failed", output=last_60_lines)`

**Commit message format:** `<type>(<scope>): <short description>` (conventional commits, ≤72 chars)

---

## Resilience Rules

- **LLM stall:** `LLMStallError` after 120s; retry up to 3 times
- **Rate limit:** switch to fallback client on HTTP 429
- **Missing developer context:** move task back to `develop` + `action:todo`
- **CI environment missing:** auto-provision `.venv`, install from `requirements.txt`
- **Path sanitization:** strip project folder name prefix from all written paths

---

## Drift Indicators

If you observe any of the following, the implementation has drifted from this spec:
- Tests make real network or database calls
- `inspect(conn.sync_connection)` used directly (not wrapped in `run_sync`)
- Alembic migrations imported directly instead of via `importlib.util`
- CI commits on a failed test run
- Test files written to wrong path (not `backend/tests/test_*.py`)
- `write_file` called with multiple files bundled together
