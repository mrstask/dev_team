"""Shared type definitions and constants for the dev team pipeline."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field


# ── Literal types matching ai-ui backend ─────────────────────────────────────

TaskStatusName = Literal[
    "backlog", "ready", "running", "review", "done", "failed",
    "architect", "develop", "testing",
]

TaskPriorityName = Literal["low", "medium", "high", "critical"]

AgentType = Literal["mock", "langchain", "langgraph", "custom"]


# ── Task status constants ─────────────────────────────────────────────────────

class Status:
    BACKLOG = "backlog"
    READY = "ready"
    RUNNING = "running"
    REVIEW = "review"
    ARCHITECT = "architect"
    DEVELOP = "develop"
    TESTING = "testing"
    DONE = "done"
    FAILED = "failed"

    ALL = [BACKLOG, READY, RUNNING, REVIEW, ARCHITECT, DEVELOP, TESTING, DONE, FAILED]


VALID_TRANSITIONS: dict[str, set[str]] = {
    Status.BACKLOG:   {Status.ARCHITECT, Status.FAILED},
    Status.ARCHITECT: {Status.DEVELOP, Status.DONE, Status.FAILED},  # DONE for parent completion
    Status.DEVELOP:   {Status.TESTING, Status.FAILED},
    Status.TESTING:   {Status.DONE, Status.DEVELOP, Status.FAILED},  # DEVELOP for rejection retry
    Status.DONE:      set(),  # terminal
    Status.FAILED:    {Status.BACKLOG},  # manual resurrection only
}


class Action:
    TODO        = "action:todo"
    REVIEW      = "action:review"
    AWAIT_HUMAN = "action:await-human"   # paused — waiting for human approve/reject
    PREFIX      = "action:"


class LabelPrefix:
    RETRY = "retry:"
    ERROR = "error:"


# ── Dashboard task (external API shape — matches ai-ui TaskRead) ─────────────

class Task(TypedDict, total=False):
    id: int
    project_id: int
    title: str
    description: str | None
    short_description: str | None
    implementation_description: str | None
    definition_of_done: str | None
    status: TaskStatusName
    priority: TaskPriorityName
    assigned_agent_id: int | None
    human_owner: str | None
    labels: list[str]
    due_date: str | None
    story_id: int | None
    parent_task_id: int | None
    queue_position: int | None
    created_at: str
    updated_at: str


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
    patterns: list[str] = Field(default_factory=list, description="Code patterns observed")
    data_flow: str = Field(default="", description="Data flow analysis")
    warnings: list[str] = Field(default_factory=list, description="Potential issues or risks")
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


# ── ReAct loop event summary — compact payload for activity events ────────────

class ToolCallRecord(BaseModel):
    """Single tool invocation within a ReAct loop."""
    model_config = ConfigDict(frozen=True)
    name: str = Field(description="Tool function name (read_file, write_file, etc.)")
    args_summary: str = Field(description="Key argument (file path, pattern, etc.)")
    ok: bool = Field(description="Whether the tool call succeeded")
    error_snippet: str = Field(default="", description="Error message if failed (up to 500 chars)")


ReactOutcome = Literal["finish_called", "max_rounds", "no_tool_calls", "error"]


class ReactLoopSummary(BaseModel):
    """Compact summary of a ReAct loop execution for activity event logging."""
    model_config = ConfigDict(frozen=True)
    round_count: int = Field(description="Number of assistant turns in the loop")
    tool_sequence: list[ToolCallRecord] = Field(default_factory=list, description="Ordered tool calls")
    files_written: list[str] = Field(default_factory=list, description="Paths written via write_file")
    errors: list[str] = Field(default_factory=list, description="Error messages encountered")
    outcome: ReactOutcome = Field(description="How the loop terminated")
    finish_summary: str = Field(default="", description="Summary passed to finish() if called")


# ── A2A communication log — local protocol envelope for inspector integration ─

class A2AAttachment(BaseModel):
    """Reference to a task artifact or context file attached to an A2A message."""
    model_config = ConfigDict(frozen=True)
    label: str = Field(description="Human-readable attachment label")
    path: str = Field(description="Absolute or repo-relative file path")
    media_type: str = Field(default="application/json", description="Attachment media type")


class A2AMessage(BaseModel):
    """Persisted internal A2A-style message used to build inspector task history."""
    model_config = ConfigDict(frozen=True)
    id: str = Field(description="Unique message identifier")
    protocol: str = Field(default="a2a-protocol.org/v0.2.6", description="A2A spec version represented")
    created_at: datetime = Field(description="UTC timestamp for the message")
    kind: str = Field(description="Message kind: request, handoff, review, decision, system")
    from_agent: str = Field(description="Logical sender")
    to_agent: str = Field(description="Logical recipient")
    task_id: int = Field(description="Dashboard task ID")
    task_title: str = Field(default="", description="Task title snapshot")
    task_status: str = Field(default="", description="Task status snapshot when message was recorded")
    priority: str = Field(default="", description="Task priority snapshot")
    parent_task_id: int | None = Field(default=None, description="Parent task ID for subtasks")
    summary: str = Field(default="", description="Short summary of the communication")
    payload: dict[str, Any] = Field(default_factory=dict, description="Structured message payload")
    attachments: list[A2AAttachment] = Field(default_factory=list, description="Referenced artifacts")


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
