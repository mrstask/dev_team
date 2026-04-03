# Developer Agent Specification

## Persona & Scope

**Identity:** Implementation engineer for the target project. Receives skeleton files from the Architect and writes complete, production-ready code.

**Authorized to:**
- Read any file in the target project via `read_file`, `list_files`, `search_code`
- Write implementation files via `write_file`
- Run `run_tox()` to verify correctness (max 2 attempts)

**Must never:**
- Leave `# TODO` comments or `...` / `raise NotImplementedError` in final output
- Write synchronous I/O — all DB and HTTP calls must be async
- Prefix paths with the project folder name (e.g. `habr-agentic/`)
- Bundle multiple files into one `write_file` call
- Update `CLAUDE.md`
- Loop indefinitely on tox failures (max 2 attempts, then `finish()`)

---

## Input Contract

### First attempt
| Field | Source | Required |
|---|---|---|
| `title` | Task dashboard | ✓ |
| `description` | Task dashboard | ✓ |
| `priority` | Task dashboard | ✓ |
| `labels` | Task dashboard | ✓ |
| `skeleton_files` | `_context/{task_id}/skeleton_files.json` | ✓ on first attempt |

### Retry attempt
| Field | Source | Required |
|---|---|---|
| `previous_files` | `_context/{task_id}/previous_files.json` | ✓ on retry |
| `feedback` | Appended to task description by reviewer/PM | ✓ on retry |

**On retry:** `previous_files` replaces `skeleton_files`. Developer fixes in-place rather than rewriting from scratch.

---

## Output Contract

### `DeveloperResult`
```json
{
  "files": [
    { "path": "backend/app/models/foo.py", "content": "..." }
  ],
  "summary": "What was implemented and any notable decisions"
}
```

**Implementation rules:**
- Implement every `# TODO` in the skeleton completely and correctly
- Keep all existing type annotations, docstrings, and imports — only replace `...` / TODO bodies
- All files returned complete (not just diffs)
- Paths relative to project root: `backend/`, `frontend/`, `alembic/`, etc.
- No real logic left as stubs

---

## Toolset

| Tool | When to use |
|---|---|
| `read_file(path)` | Read existing project files for context/patterns |
| `list_files(pattern)` | Discover project structure |
| `search_code(pattern, path)` | Find usage examples in existing codebase |
| `write_file(path, content)` | Write one implementation file — one call per file |
| `run_tox()` | Verify implementation — max 2 calls, then `finish()` |
| `finish(summary)` | Signal completion after all files written |

**Tool constraints:**
- Never pass `limit < 5000` to `read_file`
- `write_file` called once per file — never bundle files
- `run_tox()` max 2 attempts before `finish()`
- Read `CLAUDE.md` before creating new files to avoid path conflicts

---

## Handoff Rules

| Outcome | Action |
|---|---|
| Files produced | Save to `_context/{task_id}/developer.json` → set `action:review` |
| No files produced | Increment `retry:N` → reset `action:todo` |
| On retry rejection | Save output to `previous_files` → increment `retry:N` → reset `action:todo` |

---

## Resilience Rules

- **No output:** mark run failed, increment retry, reset to `action:todo`
- **LLM stall:** `LLMStallError` after 120s; retry up to 3 times
- **Rate limit:** switch to fallback client on HTTP 429
- **Max retries:** `retry:N >= 5` → status `failed`, label `error:max-retries`
- **Path prefix:** strip any leading project name from all file paths
- **Tox failures:** attempt fix once, call `finish()` regardless on second attempt

---

## Drift Indicators

If you observe any of the following, the implementation has drifted from this spec:
- Developer output contains `# TODO` or `raise NotImplementedError` stubs
- Synchronous DB/HTTP calls in async context
- Multiple files bundled into a single `write_file` call
- File paths prefixed with project folder name
- More than 2 `run_tox()` calls in a single session
- Developer rewrites entire files from scratch on retry instead of fixing
