---
description: Research the target project codebase and produce a compact research document
model: opus
---

# Research Codebase

You are tasked with conducting focused research across the target project codebase by spawning parallel sub-agents and synthesising their findings into a compact, actionable research document.

## CRITICAL: YOUR ONLY JOB IS TO DOCUMENT AND EXPLAIN THE CODEBASE AS IT EXISTS TODAY
- DO NOT suggest improvements or changes unless explicitly asked
- DO NOT perform root cause analysis unless explicitly asked
- DO NOT propose future enhancements unless explicitly asked
- DO NOT critique the implementation or identify problems
- ONLY describe what exists, where it exists, how it works, and how components interact
- You are creating a technical map of the existing system

## Codebase Layout

The **target project** lives one level above `dev_team/`. Its root is referenced as `ROOT` in `dev_team/config.py`. Typical layout:

```
ROOT/
  backend/        ← Python FastAPI/SQLAlchemy service
  frontend/       ← React/TypeScript UI
  alembic/        ← DB migrations
  CLAUDE.md       ← canonical file index (always read this first)
```

Read `CLAUDE.md` at the project root before spawning any sub-agents — it is the authoritative file index.

## Initial Setup

When this command is invoked, respond with:
```
I'm ready to research the codebase. Please provide your research question or area of interest.
```

Then wait for the user's query.

## Steps After Receiving the Research Query

### 1. Read directly mentioned files first
- If the user references specific files, read them FULLY before doing anything else
- Use the Read tool without limit/offset — always read complete files
- Do this in the main context, not in a sub-agent

### 2. Read CLAUDE.md
- Always read `CLAUDE.md` from the project root for the canonical file index
- This tells you exactly where everything lives before you start exploring

### 3. Decompose the research question and spawn parallel sub-agents

Break the query into focused research areas and spawn parallel Explore sub-agents:

- **File location agent** — find WHERE relevant files and components live
- **Implementation agent** — understand HOW specific code works (describe only, no critique)
- **Pattern agent** — find existing patterns and conventions to document
- **Data flow agent** — trace how data moves between layers (request → router → service → repo → model)

Each agent should:
- Return specific `file.py:line_number` references
- Describe what exists, not what should exist
- Focus on a narrow, well-defined area
- Use only read tools (Read, Glob, Grep)

Example sub-agent prompt style:
```
Read backend/app/models/ and describe every model: its fields, relationships,
and which routers reference it. Return file:line references. Document only —
do not suggest changes.
```

### 4. Wait for ALL sub-agents to complete, then synthesise

- Compile all findings
- Prioritise live code as primary source of truth
- Connect findings across components
- Answer the user's specific question with concrete evidence and file references

### 5. Gather git metadata
```bash
git rev-parse HEAD        # commit hash
git branch --show-current # branch name
date -u +"%Y-%m-%dT%H:%M:%SZ"  # timestamp
```

### 6. Write the research document

Save to: `plans/research/YYYY-MM-DD-{kebab-description}.md`

Examples:
- `plans/research/2025-04-04-article-feed-data-flow.md`
- `plans/research/2025-04-04-auth-middleware.md`

Document structure:

```markdown
---
date: [ISO timestamp]
git_commit: [hash]
branch: [branch name]
topic: "[User's question]"
status: complete
---

# Research: [Topic]

**Date**: [timestamp]
**Commit**: [hash]
**Branch**: [branch]

## Research Question
[Original user query verbatim]

## Summary
[2–4 sentence answer to the question, describing what exists]

## Detailed Findings

### [Component / Area 1]
- What it does ([`path/to/file.py:42`])
- How it connects to other components
- Current implementation details

### [Component / Area 2]
...

## Code References
- `backend/app/models/article.py:14` — Article model with slug, title, body
- `backend/app/routers/articles.py:88` — GET /articles endpoint

## Data Flow
[How a request flows through the system for the relevant feature]

## Conventions Found
[Naming patterns, async/sync rules, import style, error handling patterns]

## Open Questions
[Anything that needs further investigation]
```

### 7. Present findings

- Show the user where the document was saved
- Give a concise verbal summary
- Ask if they have follow-up questions

### 8. Handle follow-up questions

- Append to the same research document under `## Follow-up: [timestamp]`
- Update `last_updated` in frontmatter
- Spawn new sub-agents as needed

## Important Notes

- Always read `CLAUDE.md` before spawning sub-agents
- Always read mentioned files FULLY before spawning sub-tasks (no limit/offset)
- Always wait for ALL sub-agents to complete before synthesising
- Never write the document with placeholder values — gather all metadata first
- The `plans/research/` directory is the persistent knowledge base; keep it tidy
- Document what IS, not what SHOULD BE
