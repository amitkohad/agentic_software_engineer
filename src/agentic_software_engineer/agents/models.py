"""Typed contracts exchanged by agents and the workflow orchestrator.

These models are intentionally provider- and transport-agnostic so that agents
remain independent of LangGraph, FastAPI, OpenAI, and persistence adapters.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExecutionStatus(StrEnum):
    """Lifecycle outcome states for a single agent execution."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
    ROLLED_BACK = "rolled_back"
    AWAITING_APPROVAL = "awaiting_approval"
    CANCELLED = "cancelled"


class Artifact(BaseModel):
    """A versioned output produced, consumed, or referenced by an agent."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, description="Human-readable artifact name.")
    artifact_type: str = Field(min_length=1, description="Artifact classification.")
    location: str = Field(min_length=1, description="Logical or physical artifact location.")
    version: str | None = Field(default=None, description="Optional artifact version.")
    checksum: str | None = Field(default=None, description="Optional integrity checksum.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional artifact metadata.")


class ExecutionLog(BaseModel):
    """A structured, attributable event emitted during agent execution."""

    model_config = ConfigDict(frozen=True)

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    level: str = Field(min_length=1, description="Log severity level.")
    message: str = Field(min_length=1, description="Human-readable event description.")
    event_type: str = Field(default="agent.execution", description="Machine-readable event category.")
    attributes: dict[str, Any] = Field(default_factory=dict, description="Safe structured event fields.")


class ExecutionMetrics(BaseModel):
    """Measured resource and quality signals for a single execution."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    custom: dict[str, float | int | str | bool] = Field(default_factory=dict)


class NextAction(BaseModel):
    """A recommended, explicit action following an agent result."""

    model_config = ConfigDict(frozen=True)

    action: str = Field(min_length=1, description="Machine-readable next action identifier.")
    description: str = Field(min_length=1, description="Explanation of the next action.")
    owner: str | None = Field(default=None, description="Suggested responsible agent or human role.")
    required: bool = Field(default=True, description="Whether the action blocks workflow progress.")


class AgentResponse(BaseModel):
    """Standard, serializable result returned by every agent lifecycle operation.

    The response captures business outputs and operational evidence together,
    enabling LangGraph routing, approval gating, retries, observability, and
    audit reconstruction without parsing unstructured agent text.
    """

    model_config = ConfigDict(frozen=True)

    execution_id: str = Field(min_length=1, description="Unique lifecycle execution identifier.")
    agent_name: str = Field(min_length=1, description="Stable agent identity.")
    status: ExecutionStatus
    summary: str = Field(min_length=1, description="Concise result summary.")
    artifacts: list[Artifact] = Field(default_factory=list)
    logs: list[ExecutionLog] = Field(default_factory=list)
    metrics: ExecutionMetrics = Field(default_factory=ExecutionMetrics)
    next_actions: list[NextAction] = Field(default_factory=list)
    approval_required: bool = Field(default=False)
    confidence_score: float = Field(ge=0.0, le=1.0)
    execution_time_ms: int = Field(ge=0)
    error_code: str | None = Field(default=None)
    error_message: str | None = Field(default=None)
