"""Agent role definitions — name, description, system prompt for each dev agent."""
import config
from prompts import (
    ARCHITECT_SYSTEM_PROMPT,
    DASHBOARD_BUILDER_SYSTEM_PROMPT,
    DEVELOPER_SYSTEM_PROMPT,
    ETL_PORTER_SYSTEM_PROMPT,
    PIPELINE_BUILDER_SYSTEM_PROMPT,
    REVIEW_ENGINE_SYSTEM_PROMPT,
    TESTER_ROLE_SYSTEM_PROMPT,
    VISION_EMBEDDING_SYSTEM_PROMPT,
)

ROLES: dict[str, dict] = {
    "architect": {
        "name": "Architect",
        "description": "Produces skeleton files — typed signatures, docstrings, TODO comments. No implementation.",
        "system_prompt": ARCHITECT_SYSTEM_PROMPT,
    },

    "developer": {
        "name": "Developer",
        "description": "Implements skeleton files produced by the Architect",
        "system_prompt": DEVELOPER_SYSTEM_PROMPT,
    },

    "etl_porter": {
        "name": "ETL Porter",
        "description": "Port ETL services from source project — async rewrite, new model interfaces",
        "system_prompt": ETL_PORTER_SYSTEM_PROMPT,
    },

    "pipeline_builder": {
        "name": "Pipeline Builder",
        "description": "LangGraph StateGraph — nodes, edges, state, scheduling, graph compilation",
        "system_prompt": PIPELINE_BUILDER_SYSTEM_PROMPT,
    },

    "review_engine": {
        "name": "Review Engine Builder",
        "description": "Content filter node, review nodes, LLM prompts for quality gates",
        "system_prompt": REVIEW_ENGINE_SYSTEM_PROMPT,
    },

    "vision_embedding": {
        "name": "Vision & Embedding Builder",
        "description": "Image OCR/Russian text detection and article vectorization nodes",
        "system_prompt": VISION_EMBEDDING_SYSTEM_PROMPT,
    },

    "dashboard_builder": {
        "name": "Dashboard Builder",
        "description": "Pipeline ops dashboard — FastAPI backend routes + React 19 TypeScript frontend",
        "system_prompt": DASHBOARD_BUILDER_SYSTEM_PROMPT,
    },
    "pm": {
        "name": "Project Manager",
        "description": "Autonomous PM — reviews agent output and makes approve/reject decisions",
        "system_prompt": "",  # PM uses specialized prompts in pm_agent.py
    },

    "tester": {
        "name": "Test Engineer",
        "description": "Writes pytest unit tests for backend Python modules",
        "system_prompt": TESTER_ROLE_SYSTEM_PROMPT,
    },
}


def get_role_for_task(task: dict) -> str | None:
    """Return the agent role key for a task based on its labels."""
    for label in task.get("labels", []):
        if label in config.LABEL_TO_ROLE:
            return config.LABEL_TO_ROLE[label]
    return None
