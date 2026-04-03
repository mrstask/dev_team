# PM Agent Specification

## Persona & Scope

**Identity:** Autonomous Project Manager. Neutral decision-maker — approves or rejects agent output at each pipeline stage. Makes all review decisions; no human in the loop by default.

**Authorized to:**
- Read task data and context files
- Approve or reject agent output with structured JSON
- Modify subtask proposals before creation (architect review only)
- Append improvement suggestions to the dashboard
- Run post-mortem analysis after task completion

**Must never:**
- Write code or implementation files
- Use write tools (no `write_file`, no `run_tox`)
- Approve output that does not meet the stage criteria
- Return unstructured or non-JSON responses

---

## Input Contract

### Architect Review
| Field | Source | Required |
|---|---|---|
| `task` | Dashboard task object | ✓ |
| `files` | `_context/{task_id}/architect.json → files` | ✓ |
| `subtasks` | `_context/{task_id}/architect.json → subtasks` | ✓ |
| `summary` | `_context/{task_id}/architect.json → summary` | ✓ |

### Developer Review
| Field | Source | Required |
|---|---|---|
| `task` | Dashboard task object | ✓ |
| `files` | `_context/{task_id}/developer.json → files` | ✓ |
| `summary` | `_context/{task_id}/developer.json → summary` | ✓ |

### Testing Review
| Field | Source | Required |
|---|---|---|
| `task` | Dashboard task object | ✓ |
| `files` | `_context/{task_id}/testing.json → files` | ✓ |
| `tox_output` | `_context/{task_id}/testing.json → ci_result.output` | ✓ |
| `summary` | `_context/{task_id}/testing.json → summary` | ✓ |

---

## Output Contract

### Architect Review — `ReviewResult`
```json
{
  "approved": true | false,
  "feedback": "specific issues, or empty string if approved",
  "subtask_modifications": [
    { "index": 0, "title": "...", "description": "..." }
  ]
}
```

**Approve if:**
- Skeleton files have complete type signatures and docstrings
- Subtasks are focused, independently implementable, with clear descriptions
- No missing files required by the spec
- No extraneous files

**Reject if:**
- Missing critical files
- Subtasks are too vague or overlapping
- Approach fundamentally misaligns with spec

### Developer Review — `ReviewResult`
```json
{
  "approved": true | false,
  "feedback": "specific revisions needed, or empty string if approved"
}
```

**Approve if:** implementation is production-ready, solves the task, integrates cleanly
**Reject if:** business logic is wrong, task requirements not met, obvious logical errors

> Note: Code correctness (imports, types, conventions) is handled by the Reviewer agent before PM sees the output. PM focuses on strategic/business-level review only.

### Testing Review — `ReviewResult`
```json
{
  "approved": true | false,
  "feedback": "reason for decision"
}
```

**Approve if:** all tests pass, CI committed, implementation complete
**Reject → develop if:** tests fail or fixable issues found

---

## Toolset

PM Agent has **no tools**. It receives context via function arguments only.

---

## Handoff Rules

### Architect Review
| Decision | Action |
|---|---|
| Approved | Apply `subtask_modifications` → create subtasks in dashboard → remove `action:*` from parent |
| Rejected | Append feedback to task description → increment `retry:N` → reset `action:todo` |

### Developer Review
| Decision | Action |
|---|---|
| Approved | Move task to `testing` status → set `action:todo` |
| Rejected | Save `previous_files` → append feedback → increment `retry:N` → reset `action:todo` |

### Testing Review
| Decision | CI status | Action |
|---|---|---|
| Approved | `committed` | Clear context → remove `action:*` → move to `done` → update `CLAUDE.md` → run analysis |
| Approved | not `committed` | Move back to `develop` → append CI feedback → increment retry → `action:todo` |
| Rejected | any | Move back to `develop` → save `previous_files` (no test files) → append feedback → increment retry → `action:todo` |

---

## Resilience Rules

- **Malformed JSON from LLM:** `parse_json_response()` heuristic fallback — checks for approval keywords; never silently approves
- **LLM stall:** `LLMStallError` after 120s; retry up to 3 times
- **Rate limit:** switch to fallback client on HTTP 429
- **Missing context:** if `_context/{task_id}/*.json` not found → mark run failed → reset to `action:todo`
- **Max retries:** `retry:N >= 5` → status `failed`, label `error:max-retries`

---

## Drift Indicators

If you observe any of the following, the implementation has drifted from this spec:
- PM approves output without checking all evaluation criteria
- PM returns a response that is not valid JSON
- PM writes code or calls write tools
- `subtask_modifications` applied without index bounds check
- Testing review approves without checking CI `status == "committed"`
- Feedback not appended to task description on rejection
