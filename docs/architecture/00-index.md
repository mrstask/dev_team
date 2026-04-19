# Dev Team — Architecture Documentation

> **Template**: [arc42](https://arc42.org) · **Diagrams**: [C4 Model](https://c4model.com) rendered with [Mermaid](https://mermaid.js.org)

---

## What is arc42?

arc42 is a pragmatic, lightweight template for documenting software architectures. It was created by Gernot Starke and Peter Hruschka and is widely used in the European software engineering community. Instead of prescribing a specific notation or tool, arc42 gives you **12 numbered sections** — each answering a concrete question about the system. You fill in only what is relevant; sections you don't need stay empty.

The 12 sections progress from **why** (goals, constraints) → **what** (context, strategy) → **how** (structure, runtime, deployment) → **quality and risk** (decisions, trade-offs, glossary). Think of it as a guided conversation with your future self about the system you built.

## What is the C4 Model?

The C4 model, created by Simon Brown, provides four levels of abstraction for describing a software system — like zooming in on a map:

| Level | Question | Audience |
|-------|----------|----------|
| **Level 1 — System Context** | What does the system do and who uses it? | Non-technical stakeholders |
| **Level 2 — Container** | What are the major processes/services? | Developers, architects |
| **Level 3 — Component** | What are the key building blocks inside a container? | Developers |
| **Level 4 — Code** | How is a specific component implemented? | Developers reading code |

C4 does not prescribe a notation — it defines **semantics**. In this doc set, all C4 diagrams are written in Mermaid (supported natively in Obsidian ≥ 1.0 and most modern markdown renderers).

---

## Navigation

| # | Section | What it answers |
|---|---------|-----------------|
| [[01-introduction-goals\|01]] | Introduction & Goals | Why does this system exist? What are the top quality goals? Who are the stakeholders? |
| [[02-constraints\|02]] | Architecture Constraints | What are the non-negotiable technical and organizational limits? |
| [[03-system-context\|03]] | System Scope & Context | What is the system boundary? What external systems does it talk to? *(C4 Level 1)* |
| [[04-solution-strategy\|04]] | Solution Strategy | What fundamental decisions shape the architecture? |
| [[05-building-blocks\|05]] | Building Block View | What are the major containers and components? *(C4 Level 2 + 3)* |
| [[06-runtime-view\|06]] | Runtime View | How does a task flow through the system end-to-end? |
| [[07-deployment-view\|07]] | Deployment View | Where does the system run? What infrastructure does it need? |
| [[08-crosscutting-concepts\|08]] | Crosscutting Concepts | What patterns and principles apply across the whole system? |
| [[09-architecture-decisions\|09]] | Architecture Decisions | What were the key choices, and why? *(ADR records)* |
| [[10-quality-requirements\|10]] | Quality Requirements | How is quality defined and measured? |
| [[11-risks-technical-debt\|11]] | Risks & Technical Debt | What could go wrong? What shortcuts were taken? |
| [[12-glossary\|12]] | Glossary | What does the domain-specific terminology mean? |

---

## Using This Vault in Obsidian

- **Graph View** (`Ctrl/Cmd + G`): shows how sections link to each other via `[[wikilinks]]`. Each section links to the glossary and to related sections — expect a well-connected graph.
- **Backlinks panel**: open any file and check the backlinks pane to see which sections reference it.
- **Mermaid diagrams**: require Obsidian ≥ 1.0 (built-in renderer) or the *Mermaid* community plugin. C4-flavored Mermaid (`C4Context`, `C4Container`) requires the **"Mermaid C4"** community plugin or Obsidian ≥ 1.4.
- **Search** (`Ctrl/Cmd + Shift + F`): search across all sections for a term (e.g., `ReAct`, `FeedbackContext`).
- **Local graph**: click the graph icon on any section to see only that section's connections.

> **Tip**: If C4-specific Mermaid blocks don't render, fall back to the standard `graph LR` / `graph TB` equivalents — all diagrams degrade gracefully.

---

## About This System

**Dev Team** is a fully autonomous, event-driven multi-agent orchestration system that coordinates LLM agents across different backends (OpenRouter, Ollama, Claude Code SDK) to implement software tasks for a target project. An AI PM agent makes all review/approval decisions — no human in the loop by default.

- **Repository**: `dev_team/`
- **Entry point**: `main.py` → `event_loop.py`
- **Key config**: `models.json` (LLM backends), `.env` (API keys), `config.py` (constants)
