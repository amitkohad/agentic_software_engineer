"""Abstract lifecycle template for enterprise Agentic SDLC agents."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from agentic_software_engineer.orchestrator.state import (
    AgenticSDLCState as AgentState,
    ExecutionHistoryEntry,
    WorkflowExecutionStatus,
)


def build_openai_json_schema(schema: dict[str, Any] | type[BaseModel]) -> dict[str, Any]:
    """Return a strict JSON schema compatible with the OpenAI Responses API."""

    if isinstance(schema, dict):
        return _make_openai_strict(schema)

    return _make_openai_strict(schema.model_json_schema())


def _make_openai_strict(schema: Any) -> Any:
    """Recursively add OpenAI-compatible strictness rules for object schemas."""
    if isinstance(schema, dict):
        updated_schema = dict(schema)
        if updated_schema.get("type") == "object":
            updated_schema["additionalProperties"] = False
            properties = updated_schema.get("properties")
            if isinstance(properties, dict):
                required_properties = list(properties.keys())
                existing_required = updated_schema.get("required")
                if isinstance(existing_required, list):
                    required_properties = list(dict.fromkeys([*existing_required, *required_properties]))
                updated_schema["required"] = required_properties

        for key in ("properties", "$defs", "definitions"):
            if key in updated_schema and isinstance(updated_schema[key], dict):
                updated_schema[key] = {name: _make_openai_strict(value) for name, value in updated_schema[key].items()}

        for key in ("items", "anyOf", "allOf", "oneOf"):
            if key in updated_schema:
                value = updated_schema[key]
                if isinstance(value, list):
                    updated_schema[key] = [_make_openai_strict(item) for item in value]
                else:
                    updated_schema[key] = _make_openai_strict(value)

        return updated_schema

    if isinstance(schema, list):
        return [_make_openai_strict(item) for item in schema]

    return schema


class BaseAgent(ABC):
    """Define a SOLID, state-driven lifecycle for every AI agent.

    Subclasses implement the six narrow lifecycle hooks for their own SDLC
    responsibility. The :meth:`run` template method owns cross-cutting concerns
    such as timing, status changes, approval pauses, bounded recovery, rollback,
    metrics, audit history, and exception containment. This keeps concrete
    agents focused on one responsibility and makes the orchestration contract
    uniform across all specialist agents.

    The constructor accepts a logger, allowing the composition root to inject
    the application's logging implementation instead of coupling agents to a
    specific observability provider.
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        """Create an agent with an injectable logger.

        Args:
            logger: Application logger supplied by dependency injection. The
                module logger is used when no logger is provided.
        """
        self._logger = logger or logging.getLogger(self.__class__.__module__)

    @property
    @abstractmethod
    def name(self) -> str:
        """Return this agent's stable, machine-readable identity."""

    @abstractmethod
    async def initialize(self, state: AgentState) -> AgentState:
        """Prepare this agent to process the supplied workflow state.

        Args:
            state: Current durable state shared by all workflow agents.

        Returns:
            Updated workflow state.
        """

    @abstractmethod
    async def execute(self, state: AgentState) -> AgentState:
        """Perform the agent's approved, domain-specific SDLC work.

        Args:
            state: Current durable state shared by all workflow agents.

        Returns:
            Updated workflow state.
        """

    @abstractmethod
    async def validate(self, state: AgentState) -> AgentState:
        """Validate the artifacts and outcomes produced by this agent.

        Args:
            state: State returned by the current execution attempt.

        Returns:
            Updated workflow state containing validation outcomes.
        """

    @abstractmethod
    async def retry(self, state: AgentState) -> AgentState:
        """Perform one policy-approved recovery attempt after a failure.

        Args:
            state: Failed workflow state enriched with retry metadata.

        Returns:
            Updated workflow state from the retry attempt.
        """

    @abstractmethod
    async def rollback(self, state: AgentState) -> AgentState:
        """Compensate for the agent's incomplete or failed execution.

        Args:
            state: Failed workflow state requiring rollback.

        Returns:
            Updated workflow state with rollback evidence.
        """

    @abstractmethod
    async def report(self, state: AgentState) -> AgentState:
        """Finalize the agent's execution report and audit-facing evidence.

        Args:
            state: Current workflow state after execution or approval routing.

        Returns:
            Updated workflow state.
        """

    async def run(self, state: AgentState) -> AgentState:
        """Execute the standard agent lifecycle with consistent safeguards.

        The template runs initialization, execution, and validation. It pauses
        at a human approval boundary when ``approval_required`` is true. Any
        unexpected exception triggers one agent-owned retry attempt, followed by
        rollback if recovery also fails. This method always records elapsed time
        and returns a structured ``AgentState`` instead of leaking operational
        exceptions to the graph runtime.

        Args:
            state: Durable state received from the LangGraph workflow.

        Returns:
            The terminal, paused, or recovered state for this agent lifecycle.
        """
        started_at = perf_counter()
        active_state = self._transition(state, WorkflowExecutionStatus.RUNNING, "agent.started")

        try:
            active_state = await self.initialize(active_state)
            active_state = await self.execute(active_state)
            active_state = await self.validate(active_state)

            if active_state.approval_required:
                active_state = self._transition(
                    active_state,
                    WorkflowExecutionStatus.AWAITING_APPROVAL,
                    "agent.awaiting_approval",
                )
            else:
                active_state = self._transition(active_state, WorkflowExecutionStatus.SUCCEEDED, "agent.succeeded")

            completed_state = await self.report(active_state)
        except Exception as error:  # Concrete agents may raise provider or tool exceptions.
            self._logger.exception(
                "Agent lifecycle failed; starting recovery",
                extra={"execution_id": state.execution_id, "agent_name": self.name},
            )
            completed_state = await self._recover(active_state, error, started_at)

        elapsed_ms = int((perf_counter() - started_at) * 1_000)
        return self._record_execution_time(completed_state, elapsed_ms)

    async def _recover(self, state: AgentState, error: Exception, started_at: float) -> AgentState:
        """Perform one retry and rollback when retry recovery cannot complete."""
        retry_state = self._transition(
            state.model_copy(update={"retry_count": state.retry_count + 1}),
            WorkflowExecutionStatus.RETRYING,
            "agent.retrying",
            error_message=str(error),
        )

        try:
            recovered_state = await self.retry(retry_state)
            recovered_state = await self.validate(recovered_state)
            target_status = (
                WorkflowExecutionStatus.AWAITING_APPROVAL
                if recovered_state.approval_required
                else WorkflowExecutionStatus.SUCCEEDED
            )
            recovered_state = self._transition(recovered_state, target_status, "agent.retry_succeeded")
            return await self.report(recovered_state)
        except Exception as retry_error:
            self._logger.exception(
                "Agent recovery failed; starting rollback",
                extra={"execution_id": state.execution_id, "agent_name": self.name},
            )
            failed_state = self._transition(
                retry_state.model_copy(update={"rollback_reason": str(retry_error)}),
                WorkflowExecutionStatus.ROLLING_BACK,
                "agent.rollback_started",
                error_message=str(retry_error),
            )
            try:
                rolled_back_state = await self.rollback(failed_state)
                rolled_back_state = self._transition(
                    rolled_back_state,
                    WorkflowExecutionStatus.FAILED,
                    "agent.rollback_completed",
                    error_message=str(retry_error),
                )
                return await self.report(rolled_back_state)
            except Exception as rollback_error:
                self._logger.exception(
                    "Agent rollback failed",
                    extra={"execution_id": state.execution_id, "agent_name": self.name},
                )
                return self._transition(
                    failed_state.model_copy(update={"rollback_reason": str(rollback_error)}),
                    WorkflowExecutionStatus.FAILED,
                    "agent.rollback_failed",
                    error_message=str(rollback_error),
                )
            finally:
                elapsed_ms = int((perf_counter() - started_at) * 1_000)
                self._logger.debug(
                    "Agent recovery completed",
                    extra={"execution_id": state.execution_id, "agent_name": self.name, "elapsed_time_ms": elapsed_ms},
                )

    def _transition(
        self,
        state: AgentState,
        status: WorkflowExecutionStatus,
        event_type: str,
        *,
        error_message: str | None = None,
    ) -> AgentState:
        """Create an audited state snapshot for one lifecycle status transition."""
        updated_at = datetime.now(UTC)
        event = ExecutionHistoryEntry(
            event_id=str(uuid4()),
            timestamp=updated_at,
            agent_name=self.name,
            stage=state.current_stage,
            status=status,
            event_type=event_type,
            summary=f"{self.name} transitioned to {status.value}.",
            metadata={"error_message": error_message} if error_message else {},
        )
        return state.model_copy(
            update={
                "current_agent": self.name,
                "execution_status": status,
                "execution_history": [*state.execution_history, event],
                "timestamps": state.timestamps.model_copy(update={"updated_at": updated_at}),
            },
            deep=True,
        )

    @staticmethod
    def _record_execution_time(state: AgentState, elapsed_time_ms: int) -> AgentState:
        """Return a state copy whose aggregate elapsed-time metric is updated."""
        metrics = state.metrics.model_copy(update={"elapsed_time_ms": elapsed_time_ms})
        return state.model_copy(update={"metrics": metrics}, deep=True)
