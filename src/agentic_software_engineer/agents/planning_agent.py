"""Engineering planning agent backed by the OpenAI Responses API."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError

from agentic_software_engineer.agents.base import BaseAgent, build_openai_json_schema
from agentic_software_engineer.agents.prompt_loader import FilePromptLoader, PromptLoader
from agentic_software_engineer.orchestrator.state import (
    AgenticSDLCState as AgentState,
    Dependency,
    Task,
)


class PlanningOutput(BaseModel):
    """Strict structured result emitted by the GPT planning request."""

    tasks: list[Task] = Field(min_length=1)
    dependencies: list[Dependency] = Field(default_factory=list)


PLANNING_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "assigned_agent": {"type": ["string", "null"]},
                    "status": {"type": "string", "enum": ["pending", "ready", "running", "blocked", "completed", "failed", "skipped"]},
                    "priority": {"type": "integer", "minimum": 0},
                    "complexity": {"type": "string"},
                    "parallelizable": {"type": "boolean"},
                    "parallel_group": {"type": ["string", "null"]},
                    "metadata": {"type": "object", "properties": {}, "additionalProperties": True},
                },
                "required": ["task_id", "title", "description", "assigned_agent", "status", "priority", "complexity", "parallelizable", "parallel_group", "metadata"],
                "additionalProperties": False,
            },
        },
        "dependencies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "predecessor_task_id": {"type": "string"},
                    "successor_task_id": {"type": "string"},
                    "dependency_type": {"type": "string"},
                    "required": {"type": "boolean"},
                },
                "required": ["predecessor_task_id", "successor_task_id", "dependency_type", "required"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["tasks", "dependencies"],
    "additionalProperties": False,
}


class MalformedPlanningResponseError(ValueError):
    """Raised when the model response does not match the planning JSON contract."""


class PlanningAgent(BaseAgent):
    """Generate an executable, dependency-aware engineering plan from requirements."""

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        *,
        prompt_loader: PromptLoader | None = None,
        prompt_path: Path | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Create a planning agent with provider and prompt dependencies injected."""
        super().__init__(logger=logger)
        if not model.strip():
            raise ValueError("An OpenAI model identifier is required.")
        self._client = client
        self._model = model
        self._prompt_loader = prompt_loader or FilePromptLoader()
        self._prompt_path = prompt_path or Path(__file__).parent.parent / "prompts" / "planning_prompt.md"
        self._prompt: str | None = None

    @property
    def name(self) -> str:
        """Return the stable orchestration identity for this agent."""
        return "planning_agent"

    async def initialize(self, state: AgentState) -> AgentState:
        """Load the planning prompt before model execution."""
        prompt = self._prompt_loader.load(self._prompt_path).strip()
        if not prompt:
            raise ValueError("Planning prompt must not be empty.")
        self._prompt = prompt
        self._logger.debug("Planning prompt initialized", extra={"execution_id": state.execution_id})
        return state

    async def execute(self, state: AgentState) -> AgentState:
        """Generate typed tasks and a dependency graph from clarified requirements."""
        if self._prompt is None:
            raise RuntimeError("PlanningAgent must be initialized before execution.")
        if not state.clarified_requirements:
            raise ValueError("Planning requires at least one clarified requirement.")

        planning_input = json.dumps(
            {
                "project_name": state.project_name,
                "clarified_requirements": state.clarified_requirements,
                "assumptions": state.assumptions,
                "acceptance_criteria": state.acceptance_criteria,
            },
            ensure_ascii=False,
        )
        response = await self._client.responses.create(
            model=self._model,
            input=[
                {"role": "system", "content": self._prompt},
                {"role": "user", "content": planning_input},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "engineering_plan",
                    "strict": True,
                    "schema": build_openai_json_schema(PLANNING_OUTPUT_SCHEMA),
                }
            },
        )
        plan = self._parse_plan(response.output_text)
        metrics = state.metrics.model_copy(
            update={
                "total_input_tokens": state.metrics.total_input_tokens + self._usage_value(response, "input_tokens"),
                "total_output_tokens": state.metrics.total_output_tokens + self._usage_value(response, "output_tokens"),
            }
        )
        return state.model_copy(update={"tasks": plan.tasks, "dependencies": plan.dependencies, "metrics": metrics}, deep=True)

    async def validate(self, state: AgentState) -> AgentState:
        """Validate task identity, dependency references, and graph acyclicity."""
        task_ids = {task.task_id for task in state.tasks}
        if not task_ids:
            raise ValueError("Planning produced no engineering tasks.")
        if len(task_ids) != len(state.tasks):
            raise ValueError("Planning produced duplicate task identifiers.")

        for dependency in state.dependencies:
            if dependency.predecessor_task_id not in task_ids or dependency.successor_task_id not in task_ids:
                raise ValueError("Planning dependency references an unknown task.")
            if dependency.predecessor_task_id == dependency.successor_task_id:
                raise ValueError("Planning dependency cannot reference the same task twice.")
        if self._contains_cycle(state.dependencies, task_ids):
            raise ValueError("Planning dependency graph contains a cycle.")
        return state

    async def retry(self, state: AgentState) -> AgentState:
        """Reload the prompt and execute a single fresh planning attempt."""
        initialized_state = await self.initialize(state)
        return await self.execute(initialized_state)

    async def rollback(self, state: AgentState) -> AgentState:
        """Clear incomplete plan artifacts after a terminal planning failure."""
        return state.model_copy(update={"tasks": [], "dependencies": []}, deep=True)

    async def report(self, state: AgentState) -> AgentState:
        """Emit an operational planning summary for the orchestration layer."""
        self._logger.info(
            "Engineering planning completed",
            extra={
                "execution_id": state.execution_id,
                "status": state.execution_status.value,
                "task_count": len(state.tasks),
                "dependency_count": len(state.dependencies),
            },
        )
        return state

    @staticmethod
    def _parse_plan(raw_output: str) -> PlanningOutput:
        """Parse and validate the model's strict JSON planning result."""
        if not raw_output or not raw_output.strip():
            raise MalformedPlanningResponseError("OpenAI returned an empty planning response.")
        try:
            return PlanningOutput.model_validate_json(raw_output)
        except ValidationError as error:
            raise MalformedPlanningResponseError("OpenAI returned malformed planning JSON.") from error

    @staticmethod
    def _usage_value(response: Any, field_name: str) -> int:
        """Read a non-negative usage counter defensively from an SDK response."""
        usage = getattr(response, "usage", None)
        value = getattr(usage, field_name, 0) if usage is not None else 0
        return value if isinstance(value, int) and value >= 0 else 0

    @staticmethod
    def _contains_cycle(dependencies: list[Dependency], task_ids: set[str]) -> bool:
        """Return whether the directed dependency graph contains a cycle."""
        adjacency: dict[str, list[str]] = {task_id: [] for task_id in task_ids}
        for dependency in dependencies:
            adjacency[dependency.predecessor_task_id].append(dependency.successor_task_id)

        visited: set[str] = set()
        in_progress: set[str] = set()

        def visit(task_id: str) -> bool:
            if task_id in in_progress:
                return True
            if task_id in visited:
                return False
            visited.add(task_id)
            in_progress.add(task_id)
            has_cycle = any(visit(successor) for successor in adjacency[task_id])
            in_progress.remove(task_id)
            return has_cycle

        return any(visit(task_id) for task_id in task_ids)
