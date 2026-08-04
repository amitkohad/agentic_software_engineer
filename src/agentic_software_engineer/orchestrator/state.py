"""Pydantic state contract persisted throughout an Agentic SDLC workflow.

This module contains data declarations only. LangGraph nodes and infrastructure
adapters may serialize, hydrate, and update this model, but no orchestration or
business behavior belongs here.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from agentic_software_engineer.domain.entities.code_generation_plan import CodeGenerationPlan, GeneratedArtifact, GenerationReport


class WorkflowStage(StrEnum):
    """Named stages available in the enterprise SDLC workflow."""

    REQUIREMENTS = "requirements"
    PLANNING = "planning"
    ARCHITECTURE = "architecture"
    CODING = "coding"
    TESTING = "testing"
    SECURITY = "security"
    DOCUMENTATION = "documentation"
    APPROVAL = "approval"
    RELEASE = "release"


class WorkflowExecutionStatus(StrEnum):
    """Overall state of a workflow execution."""

    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    RETRYING = "retrying"
    ROLLING_BACK = "rolling_back"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(StrEnum):
    """Execution state of a single dependency-graph task."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Task(BaseModel):
    """A typed work item produced by the planning stage."""

    model_config = ConfigDict(frozen=True)

    task_id: str = Field(min_length=1, description="Stable task identifier.")
    title: str = Field(min_length=1, description="Short task title.")
    description: str = Field(min_length=1, description="Detailed task objective.")
    assigned_agent: str | None = Field(default=None, description="Planned agent owner.")
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    priority: int = Field(default=0, ge=0, description="Relative execution priority.")
    complexity: str = Field(default="medium", min_length=1, description="Estimated implementation complexity.")
    parallelizable: bool = Field(default=False, description="Whether the task may safely run with peers.")
    parallel_group: str | None = Field(default=None, description="Optional identifier for a concurrent work group.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Task-specific extensibility data.")


class Dependency(BaseModel):
    """A directed prerequisite edge between two planned tasks."""

    model_config = ConfigDict(frozen=True)

    predecessor_task_id: str = Field(min_length=1, description="Task that must complete first.")
    successor_task_id: str = Field(min_length=1, description="Task blocked by the predecessor.")
    dependency_type: str = Field(default="finish_to_start", description="Semantic relationship type.")
    required: bool = Field(default=True, description="Whether the dependency blocks execution.")


class GeneratedFile(BaseModel):
    """Metadata for a file created or modified in a generated project workspace."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(min_length=1, description="Project-relative file path.")
    operation: str = Field(min_length=1, description="Creation, update, deletion, or other operation.")
    checksum: str | None = Field(default=None, description="Optional content integrity checksum.")
    source_task_id: str | None = Field(default=None, description="Task responsible for the file change.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="File-specific extensibility data.")


class ValidationResult(BaseModel):
    """Structured result emitted by a test, security, or quality validation."""

    model_config = ConfigDict(frozen=True)

    validation_id: str = Field(min_length=1, description="Stable validation result identifier.")
    category: str = Field(min_length=1, description="Validation category, such as test or security.")
    passed: bool = Field(description="Whether the validation requirement passed.")
    summary: str = Field(min_length=1, description="Concise validation outcome.")
    evidence_location: str | None = Field(default=None, description="Optional location of detailed evidence.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Validation-specific extensibility data.")


class ExecutionHistoryEntry(BaseModel):
    """Immutable audit-facing record of a workflow state transition or agent action."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(min_length=1, description="Stable audit event identifier.")
    timestamp: datetime = Field(description="UTC time at which the event occurred.")
    agent_name: str | None = Field(default=None, description="Agent responsible for the event, if applicable.")
    stage: WorkflowStage | None = Field(default=None, description="Workflow stage at event time.")
    status: WorkflowExecutionStatus = Field(description="Workflow status after the event.")
    event_type: str = Field(min_length=1, description="Machine-readable event classification.")
    summary: str = Field(min_length=1, description="Human-readable event summary.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Audit-safe structured event data.")


class WorkflowMetrics(BaseModel):
    """Aggregate operational and quality measurements for an execution."""

    model_config = ConfigDict(frozen=True)

    elapsed_time_ms: int = Field(default=0, ge=0, description="Accumulated execution duration.")
    total_input_tokens: int = Field(default=0, ge=0, description="Total model input tokens consumed.")
    total_output_tokens: int = Field(default=0, ge=0, description="Total model output tokens generated.")
    total_tool_calls: int = Field(default=0, ge=0, description="Total agent tool invocations.")
    estimated_cost_usd: float = Field(default=0.0, ge=0, description="Estimated execution cost in USD.")
    custom: dict[str, float | int | str | bool] = Field(default_factory=dict, description="Additional metrics.")


class WorkflowTimestamps(BaseModel):
    """Lifecycle timestamps for a durable workflow execution."""

    model_config = ConfigDict(frozen=True)

    created_at: datetime = Field(description="UTC time at which the workflow was created.")
    updated_at: datetime = Field(description="UTC time of the most recent state update.")
    started_at: datetime | None = Field(default=None, description="UTC time at which execution began.")
    completed_at: datetime | None = Field(default=None, description="UTC time at which execution ended.")
    last_checkpoint_at: datetime | None = Field(default=None, description="UTC time of the latest recoverable checkpoint.")


class AgenticSDLCState(BaseModel):
    """Complete durable state shared by all agents in a LangGraph SDLC workflow.

    This model is the workflow's framework-neutral state boundary. It preserves
    requirements, planning outputs, technical artifacts, operational control
    data, and audit evidence across graph nodes and resumable executions.
    """

    execution_id: str = Field(min_length=1, description="Unique durable workflow execution identifier.")
    project_name: str = Field(min_length=1, description="Human-readable project name.")
    project_root: str | None = Field(default=None, description="Approved project workspace root for generated artifacts.")
    user_requirement: str = Field(min_length=1, description="Original user-provided requirement.")
    clarified_requirements: list[str] = Field(default_factory=list, description="Requirements clarified during analysis.")
    assumptions: list[str] = Field(default_factory=list, description="Explicit assumptions made by the workflow.")
    acceptance_criteria: list[str] = Field(default_factory=list, description="Verifiable conditions of acceptance.")
    tasks: list[Task] = Field(default_factory=list, description="Planned SDLC work items.")
    dependencies: list[Dependency] = Field(default_factory=list, description="Directed task prerequisite edges.")
    architecture: dict[str, Any] = Field(default_factory=dict, description="Structured architecture decisions and artifacts.")
    code_generation_plan: CodeGenerationPlan | dict[str, Any] | None = Field(default=None, description="Validated code-generation plan and artifact ordering.")
    generation_report: GenerationReport | dict[str, Any] | None = Field(default=None, description="Latest durable code-generation execution report.")
    generated_artifacts: list[GeneratedArtifact] = Field(default_factory=list, description="Validated in-memory artifacts generated by the current workflow.")
    pending_approvals: list[str] = Field(default_factory=list, description="Approval identifiers currently awaiting human decision.")
    changed_file_ids: list[str] = Field(default_factory=list, description="File IDs changed by the latest code-generation execution.")
    blocked_file_ids: list[str] = Field(default_factory=list, description="File IDs blocked by failed dependencies or policy gates.")
    architecture_version: str | None = Field(default=None, description="Version of the approved architecture used for generation.")
    code_plan_version: str | None = Field(default=None, description="Version of the active code-generation plan.")
    pending_approval_files: list[str] = Field(default_factory=list, description="File IDs awaiting required human approval.")
    approved_file_ids: list[str] = Field(default_factory=list, description="File IDs explicitly approved for generation or overwrite.")
    regeneration_targets: list[str] = Field(default_factory=list, description="Optional file paths requested for partial regeneration.")
    generated_files: list[GeneratedFile] = Field(default_factory=list, description="Generated-project file change inventory.")
    validation_results: list[ValidationResult] = Field(default_factory=list, description="Quality, test, and security outcomes.")
    documentation: dict[str, Any] = Field(default_factory=dict, description="Structured documentation artifacts and references.")
    current_agent: str | None = Field(default=None, description="Agent currently responsible for the next action.")
    current_stage: WorkflowStage | None = Field(default=None, description="Current SDLC stage.")
    execution_status: WorkflowExecutionStatus = Field(default=WorkflowExecutionStatus.PENDING)
    approval_required: bool = Field(default=False, description="Whether human approval blocks progression.")
    retry_count: int = Field(default=0, ge=0, description="Number of recovery attempts across the workflow.")
    rollback_reason: str | None = Field(default=None, description="Reason for the latest rollback request or action.")
    execution_history: list[ExecutionHistoryEntry] = Field(default_factory=list, description="Append-only workflow audit history.")
    metrics: WorkflowMetrics = Field(default_factory=WorkflowMetrics, description="Aggregate execution metrics.")
    timestamps: WorkflowTimestamps = Field(description="Workflow lifecycle timestamps.")
