"""State persistence boundary for durable Agentic SDLC workflow state."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any, TypeAlias

from agentic_software_engineer.orchestrator.state import AgenticSDLCState

AgentState: TypeAlias = AgenticSDLCState
"""Compatibility name for the shared state stored for an agent execution."""


class StateStore(ABC):
    """Abstract persistence contract used by orchestrators and application use cases.

    This interface allows the in-memory implementation to be replaced by a
    database-backed adapter without changing callers or domain workflow models.
    """

    @abstractmethod
    def save_execution_state(self, state: AgentState) -> AgentState:
        """Create or replace the state associated with its execution identifier."""

    @abstractmethod
    def load_execution_state(self, execution_id: str) -> AgentState | None:
        """Load state for an execution, or return ``None`` when it is absent."""

    @abstractmethod
    def update_partial_state(
        self,
        execution_id: str,
        updates: Mapping[str, Any],
    ) -> AgentState | None:
        """Apply a validated, top-level partial update to stored execution state."""

    @abstractmethod
    def delete_state(self, execution_id: str) -> bool:
        """Delete an execution state and return whether a value was removed."""

    @abstractmethod
    def list_active_executions(self) -> Sequence[AgentState]:
        """Return snapshots for executions that have not reached a terminal state."""
