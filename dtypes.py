"""Shared type definitions and constants for the dev team pipeline."""
from typing import TypedDict


# ── Task status constants ─────────────────────────────────────────────────────

class Status:
    BACKLOG = "backlog"
    ARCHITECT = "architect"
    DEVELOP = "develop"
    TESTING = "testing"
    DONE = "done"
    FAILED = "failed"

    ALL = [BACKLOG, ARCHITECT, DEVELOP, TESTING, DONE, FAILED]


class Action:
    TODO = "action:todo"
    REVIEW = "action:review"
    PREFIX = "action:"


class LabelPrefix:
    RETRY = "retry:"
    ERROR = "error:"


# ── TypedDicts ────────────────────────────────────────────────────────────────

class Task(TypedDict, total=False):
    id: int
    title: str
    description: str
    status: str
    priority: str
    labels: list[str]
    parent_task_id: int | None
    project_id: int
    assigned_agent_id: int | None


class FileContent(TypedDict):
    path: str
    content: str


class AgentResult(TypedDict, total=False):
    status: str            # "pending_review", "committed", "failed", "commit_failed"
    files: list[FileContent]
    summary: str
    subtasks: list[dict]   # architect only
    sha: str               # CI only
    commit_message: str    # CI only
    output: str            # CI failure output


class ReviewResult(TypedDict, total=False):
    approved: bool
    issues: list[str]
    overall_comment: str
    feedback: str
    subtask_modifications: list[dict]
