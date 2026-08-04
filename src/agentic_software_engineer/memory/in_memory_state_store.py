"""Thread-safe in-memory implementation of the shared workflow state store."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from threading import RLock
from typing import Any, ClassVar, Self

from pydantic import ValidationError

from agentic_software_engineer.application.ports.state_store import AgentState, StateStore
from agentic_software_engineer.orchestrator.state import WorkflowExecutionStatus


class InMemorySharedStateStore(StateStore):
    """Store workflow state in process memory with singleton and DI support.

    The store is appropriate for local development and single-process test
    environments only: its contents are lost on process restart and cannot be
    shared between processes. Application composition should inject this class
    through the :class:`StateStore` interface; :meth:`get_instance` provides a
    thread-safe singleton for compositions that require one shared instance.

    Stored and returned values are deep Pydantic copies. This prevents callers
    from changing a state snapshot outside the store's lock and preserves the
    thread-safety guarantee for mutable nested fields.
    """

    _instance: ClassVar[Self | None] = None
    _instance_lock: ClassVar[RLock] = RLock()
    _terminal_statuses: ClassVar[frozenset[WorkflowExecutionStatus]] = frozenset(
        {
            WorkflowExecutionStatus.SUCCEEDED,
            WorkflowExecutionStatus.FAILED,
            WorkflowExecutionStatus.CANCELLED,
        }
    )

    def __init__(self, logger: logging.Logger | None = None) -> None:
        """Initialize an empty state store with an injectable application logger.

        Args:
            logger: Logger used for lifecycle and operational events. When not
                supplied, the module logger is used.
        """
        self._states: dict[str, AgentState] = {}
        self._lock = RLock()
        self._logger = logger or logging.getLogger(__name__)

    @classmethod
    def get_instance(cls, logger: logging.Logger | None = None) -> Self:
        """Return the process-wide, thread-safe shared state store instance.

        Args:
            logger: Optional logger used only when the singleton is first built.

        Returns:
            The singleton state store instance.
        """
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(logger=logger)
            return cls._instance

    def save_execution_state(self, state: AgentState) -> AgentState:
        """Create or replace a workflow state snapshot.

        Args:
            state: Valid Pydantic workflow state to persist in memory.

        Returns:
            A deep copy of the persisted state snapshot.
        """
        stored_state = state.model_copy(deep=True)
        with self._lock:
            operation = "updated" if state.execution_id in self._states else "created"
            self._states[state.execution_id] = stored_state

        self._logger.info(
            "Execution state %s",
            operation,
            extra={"execution_id": state.execution_id, "project_name": state.project_name},
        )
        return stored_state.model_copy(deep=True)

    def load_execution_state(self, execution_id: str) -> AgentState | None:
        """Load an isolated snapshot for an execution identifier.

        Args:
            execution_id: Unique workflow execution identifier.

        Returns:
            A deep state copy, or ``None`` if the execution is not stored.
        """
        with self._lock:
            state = self._states.get(execution_id)

        if state is None:
            self._logger.debug("Execution state not found", extra={"execution_id": execution_id})
            return None

        self._logger.debug("Execution state loaded", extra={"execution_id": execution_id})
        return state.model_copy(deep=True)

    def update_partial_state(
        self,
        execution_id: str,
        updates: Mapping[str, Any],
    ) -> AgentState | None:
        """Apply a validated top-level partial update to an execution state.

        The complete merged payload is validated by Pydantic before it replaces
        the stored snapshot. Nested objects are replaced as whole fields; deep
        merge semantics remain an explicit application-layer decision.

        Args:
            execution_id: Unique workflow execution identifier.
            updates: Field names and replacement values to apply.

        Returns:
            A deep copy of the updated state, or ``None`` if no state exists.

        Raises:
            ValidationError: If the merged state does not satisfy the Pydantic
                workflow state contract.
        """
        with self._lock:
            current_state = self._states.get(execution_id)
            if current_state is None:
                self._logger.debug("State update ignored; execution not found", extra={"execution_id": execution_id})
                return None

            merged_state = current_state.model_dump(mode="python")
            merged_state.update(updates)
            updated_state = AgentState.model_validate(merged_state)
            self._states[execution_id] = updated_state.model_copy(deep=True)

        self._logger.info(
            "Execution state partially updated",
            extra={"execution_id": execution_id, "updated_fields": sorted(updates)},
        )
        return updated_state.model_copy(deep=True)

    def delete_state(self, execution_id: str) -> bool:
        """Delete state for an execution.

        Args:
            execution_id: Unique workflow execution identifier.

        Returns:
            ``True`` when a stored execution was removed; otherwise ``False``.
        """
        with self._lock:
            removed = self._states.pop(execution_id, None) is not None

        self._logger.info(
            "Execution state delete requested",
            extra={"execution_id": execution_id, "removed": removed},
        )
        return removed

    def list_active_executions(self) -> Sequence[AgentState]:
        """Return isolated snapshots of all non-terminal workflow executions.

        Returns:
            Deep copies of state snapshots whose execution status has not reached
            a terminal workflow outcome.
        """
        with self._lock:
            active_states = [
                state.model_copy(deep=True)
                for state in self._states.values()
                if state.execution_status not in self._terminal_statuses
            ]

        self._logger.debug("Active executions listed", extra={"count": len(active_states)})
        return active_states
