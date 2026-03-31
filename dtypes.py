"""Shared type definitions and constants for the dev team pipeline."""
from __future__ import annotations

from typing import TypedDict

from pydantic import BaseModel, Field


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


# ── Dashboard task (external API shape — stays as TypedDict) ─────────────────

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


# ── Pydantic models for agent communication ─────────────────────────────────

class FileContent(BaseModel, frozen=True):
    path: str
    content: str


class SubtaskProposal(BaseModel):
    title: str
    description: str
    priority: str
    labels: list[str] = Field(default_factory=lambda: ["developer"])


class ArchitectResult(BaseModel):
    files: list[FileContent]
    summary: str
    subtasks: list[SubtaskProposal]


class DeveloperResult(BaseModel):
    files: list[FileContent]
    summary: str


class ReviewResult(BaseModel):
    approved: bool
    issues: list[str] = []
    overall_comment: str = ""
    feedback: str = ""
    subtask_modifications: list[dict] = []


class TestResult(BaseModel):
    files: list[FileContent]


class CIResult(BaseModel):
    status: str  # "committed" | "failed" | "commit_failed"
    sha: str | None = None
    commit_message: str | None = None
    output: str | None = None
