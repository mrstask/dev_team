"""Agent role definitions — namespaced by agent: pm, architect, developer, tester."""
import config
from prompts.research import RESEARCH_SYSTEM_PROMPT
from prompts import (
    ARCHITECT_DEV_REVIEW_SYSTEM_PROMPT,
    ARCHITECT_SYSTEM_PROMPT,
    DEVELOPER_SYSTEM_PROMPT,
    PM_ARCHITECT_REVIEW,
    PM_DEVELOPER_REVIEW,
    PM_TESTING_REVIEW,
    PM_USER_STORY_SYSTEM_PROMPT,
    TESTER_AGENT_SYSTEM_PROMPT,
    TESTER_CI_SYSTEM_PROMPT,
    TESTER_INTEGRATION_SYSTEM_PROMPT,
)

ROLES: dict[str, dict] = {
    # ── PM ────────────────────────────────────────────────────────────────────
    "pm:user-story": {
        "name": "PM — User Story",
        "description": "Create structured user stories from raw requirements",
        "system_prompt": PM_USER_STORY_SYSTEM_PROMPT,
        "step": "pm",
    },
    "pm:architect-review": {
        "name": "PM — Architect Review",
        "description": "Strategic review of skeleton files and subtask proposals",
        "system_prompt": PM_ARCHITECT_REVIEW,
        "step": "pm",
    },
    "pm:dev-review": {
        "name": "PM — Dev Review",
        "description": "Strategic review of developer implementation from project perspective",
        "system_prompt": PM_DEVELOPER_REVIEW,
        "step": "pm",
    },
    "pm:testing-review": {
        "name": "PM — Testing Review",
        "description": "Final review of testing and CI results before marking a task done",
        "system_prompt": PM_TESTING_REVIEW,
        "step": "pm",
    },

    # ── Researcher ───────────────────────────────────────────────────────────
    "researcher:explore": {
        "name": "Research Agent",
        "description": "Read-only codebase exploration producing a compact research artifact",
        "system_prompt": RESEARCH_SYSTEM_PROMPT,
        "step": "researcher",
    },

    # ── Architect ─────────────────────────────────────────────────────────────
    "architect:design": {
        "name": "Architect — Design",
        "description": "Produce skeleton files with typed signatures, docstrings, and TODOs",
        "system_prompt": ARCHITECT_SYSTEM_PROMPT,
        "step": "architect",
    },
    "architect:dev-review": {
        "name": "Architect — Dev Review",
        "description": "Code review of developer implementation against task specification",
        "system_prompt": ARCHITECT_DEV_REVIEW_SYSTEM_PROMPT,
        "step": "architect",
    },

    # ── Developer ─────────────────────────────────────────────────────────────
    "developer:implement": {
        "name": "Developer — Implement",
        "description": "Implement skeleton files produced by the Architect",
        "system_prompt": DEVELOPER_SYSTEM_PROMPT,
        "step": "developer",
    },
    "developer:review": {
        "name": "Developer — Review",
        "description": "Self-review of implementation quality and correctness",
        "system_prompt": "",  # TODO: implement
        "step": "developer",
    },

    # ── Tester ────────────────────────────────────────────────────────────────
    "tester:unit-tests": {
        "name": "Tester — Unit Tests",
        "description": "Write pytest unit tests for backend Python modules",
        "system_prompt": TESTER_AGENT_SYSTEM_PROMPT,
        "step": "tester",
    },
    "tester:integration-tests": {
        "name": "Tester — Integration Tests",
        "description": "Write pytest integration tests covering cross-module behaviour",
        "system_prompt": TESTER_INTEGRATION_SYSTEM_PROMPT,
        "step": "tester",
    },
    "tester:ci": {
        "name": "Tester — CI",
        "description": "Write files to disk, run tox, commit on green",
        "system_prompt": TESTER_CI_SYSTEM_PROMPT,
        "step": "tester",
    },
}


def get_role_for_task(task: dict) -> str | None:
    """Return the agent role key for a task based on its labels."""
    for label in task.get("labels", []):
        if label in config.LABEL_TO_ROLE:
            return config.LABEL_TO_ROLE[label]
    return None
