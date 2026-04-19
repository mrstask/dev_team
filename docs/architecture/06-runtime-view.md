# 06 — Runtime View

> **arc42 question**: *How does the system behave at runtime? What happens when a task runs?*

← [[05-building-blocks]] | Next: [[07-deployment-view]] →

---

Runtime view describes the **dynamic behavior** — who calls whom, in what order, and what data is passed. This complements the static structure in [[05-building-blocks]].

---

## 6.1 Happy Path: Full Task Lifecycle

This is the normal flow for a task that passes all reviews on the first attempt.

```mermaid
sequenceDiagram
    autonumber
    participant Op as Operator
    participant EL as Event Loop
    participant DA as Dashboard API
    participant RA as ResearchAgent
    participant AA as ArchitectAgent
    participant PM as PMAgent
    participant DV as DevAgent
    participant TA as TestAgent
    participant FS as Filesystem / Git

    Op->>DA: kick <task_id>  →  status: architect, action:todo
    loop Poll every 10s
        EL->>DA: GET /tasks (actionable)
        DA-->>EL: task (architect + action:todo)
    end

    Note over EL,RA: Stage 1 — Research
    EL->>RA: run(task)
    RA->>FS: read_file / list_files / search_code (read-only)
    RA-->>EL: ResearchContext (relevant_files, patterns, data_flow)
    EL->>EL: save _context/<id>/research.json

    Note over EL,AA: Stage 2 — Architect Design
    EL->>AA: run(task, research)
    AA->>FS: read_file / write_file (skeleton stubs)
    AA-->>EL: ArchitectResult (skeleton files, plan, subtask proposals)
    EL->>EL: save _context/<id>/architect.json
    EL->>DA: PATCH status: architect + action:review

    Note over EL,PM: Stage 3 — Architect Review
    EL->>PM: review_architect(task, architect_result)
    PM-->>EL: ReviewResult (approved=True)
    EL->>DA: create subtasks (develop + action:todo)
    EL->>DA: PATCH parent task — no action label

    Note over EL,DV: Stage 4 — Developer
    EL->>DV: run(subtask, skeleton_files)
    DV->>FS: read_file / write_file / run_pytest (optional)
    DV-->>EL: DeveloperResult (implemented files)
    EL->>EL: save _context/<id>/developer.json
    EL->>DA: PATCH status: develop + action:review

    Note over EL,PM: Stage 5 — Developer Review (auto-approve)
    EL->>DA: PATCH status: testing + action:todo

    Note over EL,TA: Stage 6 — Testing + CI
    EL->>TA: run(subtask, impl_files)
    TA->>FS: write_file (test files)
    TA-->>EL: TestResult
    EL->>TA: run_ci(subtask, all_files)
    TA->>FS: write all files to disk
    TA->>FS: pytest → PASS
    TA->>FS: pylint (advisory)
    TA->>FS: git commit
    TA-->>EL: CIResult (status: committed, sha: abc123)
    EL->>EL: save _context/<id>/testing.json
    EL->>DA: PATCH status: testing + action:review

    Note over EL,PM: Stage 7 — PM Final Review
    EL->>PM: review_testing(task, testing_ctx)
    PM-->>EL: ReviewResult (approved=True)
    EL->>EL: clear _context/<id>/
    EL->>DA: PATCH status: done
```

---

## 6.2 Retry Loop (PM Rejection)

When PM rejects developer output, the task is reset and retried with structured feedback.

```mermaid
sequenceDiagram
    autonumber
    participant EL as Event Loop
    participant PM as PMAgent
    participant DA as Dashboard API
    participant DV as DevAgent

    EL->>PM: review_testing(task, testing_ctx)
    PM-->>EL: ReviewResult (approved=False, issues=[...])

    Note over EL: Rejection handling
    EL->>EL: increment retry:N label (max 5)
    EL->>EL: save FeedbackEntry to feedback.json
    EL->>DA: PATCH status: develop + action:todo

    Note over EL,DV: Next poll cycle — retry
    EL->>DV: run(task, skeleton_files, previous_files, feedback_ctx)
    Note over DV: Prompt includes structured feedback<br/>from all prior rejections
    DV-->>EL: DeveloperResult (revised files)

    alt Max retries (5) exceeded
        EL->>DA: PATCH status: failed + label: error:max-retries
    end
```

---

## 6.3 CI Iterative Fix Loop

When pytest fails on the first run, TestAgent enters an iterative fix loop (up to 3 rounds).

```mermaid
sequenceDiagram
    autonumber
    participant TA as TestAgent
    participant FS as Filesystem

    TA->>FS: write all files to disk
    TA->>FS: pytest → FAIL (failing test list)

    loop Up to 3 fix rounds
        TA->>TA: ReAct loop to fix failing tests
        Note over TA: Reads failing test names,<br/>edits test or impl file
        TA->>FS: write_file (fixed file)
        TA->>FS: pytest → result
        alt PASS
            TA->>FS: pylint (advisory)
            TA->>FS: git commit
            TA-->>TA: CIResult(status: committed)
            break
        end
    end

    alt Still failing after 3 rounds
        TA-->>TA: CIResult(status: failed, output: pytest output)
    end
```

---

## 6.4 Human Gate Flow (Optional)

When `HUMAN_GATES["architect_output"] = True` in `config.py`, the pipeline pauses after architect review.

```mermaid
sequenceDiagram
    autonumber
    participant Op as Operator
    participant EL as Event Loop
    participant DA as Dashboard API

    EL->>DA: PATCH status: architect + action:await-human

    Note over EL,DA: Event loop sees action:await-human — skips task
    Note over Op: Operator inspects _context/<id>/architect.json

    alt Operator approves
        Op->>DA: approve <id>  →  removes await-human, adds action:review
    else Operator rejects
        Op->>DA: reject <id> "feedback"  →  action:todo + feedback appended
    end

    EL->>DA: next poll picks up action:review or action:todo
```

---

## 6.5 LLM Rate-Limit Fallback (Inside ReAct Loop)

```mermaid
sequenceDiagram
    autonumber
    participant RL as ReAct Loop
    participant PrimaryLLM as Primary Model
    participant FallbackLLM as Fallback Model

    RL->>PrimaryLLM: streaming chat completion
    PrimaryLLM-->>RL: HTTP 429 (rate limited)
    Note over RL: LLMRateLimitError raised
    RL->>FallbackLLM: same request (fallback_client)
    FallbackLLM-->>RL: streaming response
    Note over RL: Transparent to agent — same tool-call flow continues
```

---

> For the static structure that supports these flows, see [[05-building-blocks]].
> For the patterns that make these flows reliable, see [[08-crosscutting-concepts]].
