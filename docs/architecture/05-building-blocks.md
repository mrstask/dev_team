# 05 — Building Block View

> **arc42 question**: *What are the major structural elements and how do they relate?*
> **C4 levels**: Level 2 (Containers) + Level 3 (Components inside `core/`)

← [[04-solution-strategy]] | Next: [[06-runtime-view]] →

---

## 5.1 What Are These Diagrams?

**C4 Level 2 — Container diagram**: zooms into the Dev Team system box from [[03-system-context]] and shows the major runtime units — processes, services, or significant libraries — and how they communicate.

**C4 Level 3 — Component diagram**: zooms into one container (here, the `core/` library) and shows its internal building blocks.

> A *container* in C4 is not a Docker container. It's any deployable unit that runs in its own process space or is a distinct library.

---

## 5.2 Container Diagram (C4 Level 2)

```mermaid
graph TB
    subgraph devteam["Dev Team (Python process)"]
        eventloop["Event Loop\nevent_loop.py\n\nStateless polling daemon\nDispatches tasks to agents\nManages state transitions"]

        subgraph agents["Agents"]
            research["ResearchAgent\nagents/research.py\n\nRead-only codebase\nexploration"]
            architect["ArchitectAgent\nagents/architect.py\n\nSkeleton files + plan\n+ subtask proposals"]
            developer["DevAgent\nagents/developer.py\n\nImplements TODOs\nin skeleton files"]
            tester["TestAgent\nagents/tester.py\n\nGenerates tests\n+ runs CI (pytest+git)"]
            pm["PMAgent\nagents/pm.py\n\nAutonomous reviewer\nApprove / reject"]
        end

        subgraph core["Core Library (core/)"]
            react["ReAct Loop\ncore/react_loop.py\n\nShared tool-calling\nloop for all agents"]
            llmfactory["LLM Client Factory\ncore/llm.py\n\nCreates backend-specific\nclients + fallback"]
            tools["Tool Implementations\ncore/tools.py\n\nread_file, write_file,\nrun_pytest, search_code..."]
            roles["Role Definitions\ncore/roles.py\n\n13 agent roles with\nsystem prompts"]
        end

        subgraph clients["Clients"]
            dashclient["DashboardClient\nclients/dashboard_client.py"]
            orclient["OpenRouterClient\nclients/openrouter_client.py"]
            ollclient["OllamaClient\nclients/ollama_client.py"]
        end

        a2agw["A2A Gateway\na2a_server.py\n\nPublishes inter-agent\nhandoff messages"]
    end

    dashboard["Dashboard API\nlocalhost:8000"]
    openrouter["OpenRouter API"]
    ollama["Ollama Server\nlocalhost:11434"]
    inspector["A2A Inspector\nlocalhost:5556"]
    filesystem["Target Project\nFilesystem"]
    git["Git"]

    eventloop -->|"dispatches to"| agents
    agents -->|"use"| react
    react -->|"creates client via"| llmfactory
    react -->|"executes tools via"| tools
    agents -->|"uses role from"| roles
    tools -->|"accesses"| filesystem
    tools -->|"runs"| git
    llmfactory -->|"creates"| orclient
    llmfactory -->|"creates"| ollclient
    orclient -->|"HTTP streaming"| openrouter
    ollclient -->|"HTTP streaming"| ollama
    eventloop -->|"polls + updates"| dashclient
    dashclient -->|"REST"| dashboard
    eventloop -->|"publishes"| a2agw
    a2agw -->|"ZMQ"| inspector
```

---

## 5.3 Agent Summary Table

| Agent              | File                  | Role key                          | Backend step | Tool set                                                                       | Output model              |
| ------------------ | --------------------- | --------------------------------- | ------------ | ------------------------------------------------------------------------------ | ------------------------- |
| **ResearchAgent**  | `agents/research.py`  | `researcher:explore`              | `researcher` | read_file, list_files, search_code, submit_research                            | `ResearchContext`         |
| **ArchitectAgent** | `agents/architect.py` | `architect:design`                | `architect`  | read_file, list_files, search_code, write_file, finish                         | `ArchitectResult`         |
| **DevAgent**       | `agents/developer.py` | `developer:implement`             | `developer`  | read_file, list_files, search_code, write_file, run_pytest, run_pylint, finish | `DeveloperResult`         |
| **TestAgent**      | `agents/tester.py`    | `tester:unit-tests` + `tester:ci` | `tester`     | read_file, list_files, search_code, write_file, run_pytest, run_pylint, finish | `TestResult` + `CIResult` |
| **PMAgent**        | `agents/pm.py`        | `pm:architect-review` etc.        | `pm`         | *(no tools — single LLM call)*                                                 | `ReviewResult`            |

> Note: ArchitectAgent intentionally **cannot** call `run_pytest` or `run_pylint` — architects design, they don't test. This is enforced via `ARCHITECT_TOOL_SPECS` in `core/tools.py`.

---

## 5.4 Component Diagram — `core/` Library (C4 Level 3)

```mermaid
graph LR
    subgraph core["core/ — shared infrastructure"]
        react["react_loop.py\nrun_react_loop()\n\nIterates: LLM call →\ntool dispatch → repeat\nMax 100 rounds\nStall detection (180s)\nRate-limit fallback"]

        llm["llm.py\ncreate_client(step)\ncreate_fallback_client(step)\nstream_chat_with_display()\nparse_json_response()\n\nFactory: reads models.json\nStreams with live preview\n3-strategy JSON parsing"]

        tools["tools.py\ndispatch(name, args)\ntool_scope()\nproject_context(root)\n\nAll 7 tool implementations\nThread-local state\nRollback on failure"]

        roles["roles.py\nROLES dict (13 entries)\n\nMaps role key → \nsystem_prompt + step"]

        specloader["spec_loader.py\nload_role_spec()\n\nLoads role spec for\nreview contracts"]
    end

    react -->|"calls"| llm
    react -->|"dispatches tools via"| tools
    react -->|"reads roles from"| roles
    llm -->|"reads config from"| models["models.json"]
    specloader -->|"reads"| roles
```

---

## 5.5 Tool Specifications

Tools are defined as OpenAI-compatible function dicts. Three subsets are used:

| Constant | File | Contents |
|----------|------|---------|
| `TOOL_SPECS` | `core/tools.py` | Full set: read_file, list_files, search_code, write_file, run_pytest, run_pylint, finish |
| `ARCHITECT_TOOL_SPECS` | `core/tools.py` | Excludes run_pytest and run_pylint |
| `RESEARCH_TOOL_SPECS` | `core/tools.py` | Read-only: read_file, list_files, search_code, submit_research |

---

## 5.6 Context Artifact Files

Between pipeline stages, agent output is persisted in `_context/<task_id>/`:

| File | Pydantic model | Written by | Read by |
|------|---------------|-----------|--------|
| `research.json` | `ResearchContext` | ResearchAgent | ArchitectAgent |
| `architect.json` | `ArchitectResult` | ArchitectAgent | PMAgent, Event Loop |
| `skeleton_files.json` | `list[FileContent]` | Event Loop (from architect) | DevAgent |
| `developer.json` | `DeveloperResult` | DevAgent | TestAgent, PMAgent |
| `previous_files.json` | `list[FileContent]` | Event Loop (on retry) | DevAgent |
| `testing.json` | `TestingContext` | TestAgent | PMAgent |
| `feedback.json` | `FeedbackContext` | Event Loop (on rejection) | DevAgent |
| `error.log` | *(plain text)* | Event Loop (on exception) | Operator |

---

> See [[06-runtime-view]] to understand how these containers interact at runtime during a task execution.
> See [[08-crosscutting-concepts]] for the patterns that cut across all containers.
