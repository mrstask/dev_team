"""Shared type definitions and constants for the dev team pipeline."""
from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field


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
    TODO        = "action:todo"
    REVIEW      = "action:review"
    AWAIT_HUMAN = "action:await-human"   # paused — waiting for human approve/reject
    PREFIX      = "action:"


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

class FileContent(BaseModel):
    model_config = ConfigDict(frozen=True)
    path: str = Field(description="File path relative to project root")
    content: str = Field(description="Full file content")


class SubtaskProposal(BaseModel):
    model_config = ConfigDict(frozen=True)
    title: str = Field(description="Short title for the subtask")
    description: str = Field(description="Implementation instructions for the developer")
    priority: str = Field(default="medium", description="Task priority: critical, high, medium, low")
    labels: list[str] = Field(default_factory=lambda: ["developer"], description="Task labels")


class ArchitectResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    files: list[FileContent] = Field(description="Skeleton files produced by the architect")
    summary: str = Field(description="Architect summary including PLAN and SUBTASKS sections")
    subtasks: list[SubtaskProposal] = Field(default_factory=list, description="Proposed subtasks")
    plan: str = Field(default="", description="PLAN section extracted from summary")


class DeveloperResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    files: list[FileContent] = Field(description="Implemented files")
    summary: str = Field(default="", description="Developer summary of changes")


class ReviewResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    approved: bool = Field(description="Whether the review passed")
    issues: list[str] = Field(default_factory=list, description="Specific issues found")
    overall_comment: str = Field(default="", description="One-sentence summary")
    feedback: str = Field(default="", description="Detailed feedback for retry")
    subtask_modifications: list[dict] = Field(default_factory=list, description="Proposed subtask changes")


class TestResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    files: list[FileContent] = Field(default_factory=list, description="Generated test files")


CIStatus = Literal["committed", "failed", "commit_failed"]


class CIResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: CIStatus = Field(description="CI outcome: committed, failed, or commit_failed")
    sha: str | None = Field(default=None, description="Git commit SHA on success")
    commit_message: str | None = Field(default=None, description="Commit message used")
    output: str | None = Field(default=None, description="CI command output (pytest + pylint)")


# ── Context models — validated on save/load between pipeline stages ──────────

class ResearchContext(BaseModel):
    """Research agent output — structured codebase exploration findings."""
    relevant_files: list[str] = Field(default_factory=list, description="File paths relevant to the task")
    patterns: str = Field(default="", description="Code patterns observed")
    data_flow: str = Field(default="", description="Data flow analysis")
    warnings: str = Field(default="", description="Potential issues or risks")
    summary: str = Field(default="", description="Executive summary of findings")


class TestingContext(BaseModel):
    """Persisted between testing:todo and testing:review stages."""
    files: list[FileContent] = Field(description="All files (implementation + tests)")
    ci_result: CIResult = Field(description="CI run outcome")
    summary: str = Field(default="", description="Developer summary carried forward")


class FeedbackEntry(BaseModel):
    """Single feedback record from a review."""
    model_config = ConfigDict(frozen=True)
    source: str = Field(description="Who provided the feedback (PM, architect, human)")
    stage: str = Field(default="", description="Pipeline stage when feedback was given")
    retry: int = Field(default=0, description="Retry count when feedback was given")
    issues: list[str] = Field(default_factory=list, description="Specific issues raised")


class FeedbackContext(BaseModel):
    """Accumulated feedback across retries."""
    entries: list[FeedbackEntry] = Field(default_factory=list)


# ── Models.json validation ───────────────────────────────────────────────────

Backend = Literal["openrouter", "ollama", "claude-code"]


class StepFallback(BaseModel):
    model_config = ConfigDict(frozen=True)
    backend: Backend = Field(description="Fallback LLM backend")
    model: str = Field(min_length=1, description="Fallback model identifier")


class StepConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    backend: Backend = Field(description="LLM backend for this step")
    model: str = Field(min_length=1, description="Model identifier")
    fallback: StepFallback | None = Field(default=None, description="Fallback backend/model")


class ModelsConfig(BaseModel):
    """Validated configuration from models.json. All required steps must be present."""
    model_config = ConfigDict(frozen=True)
    researcher: StepConfig
    architect: StepConfig
    pm: StepConfig
    developer: StepConfig
    tester: StepConfig
