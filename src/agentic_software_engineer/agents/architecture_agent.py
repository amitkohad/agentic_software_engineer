"""Enterprise architecture agent backed by the OpenAI Responses API."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from pydantic import ValidationError

from agentic_software_engineer.agents.base import BaseAgent, build_openai_json_schema
from agentic_software_engineer.agents.prompt_loader import FilePromptLoader, PromptLoader
from agentic_software_engineer.domain.entities.architecture_specification import ArchitectureSpecification
from agentic_software_engineer.orchestrator.state import AgenticSDLCState as AgentState


class MalformedArchitectureResponseError(ValueError):
    """Raised when an OpenAI response cannot form a valid architecture specification."""


class ArchitectureAgent(BaseAgent):
    """Transform approved requirements and plans into a typed architecture artifact.

    All provider, model, prompt, and observability dependencies are injected so
    the agent can be tested without external configuration. The base lifecycle
    performs standard status transitions, audit-history entries, retries, timing,
    and exception containment; this agent owns architecture generation only.
    """

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        *,
        prompt_loader: PromptLoader | None = None,
        prompt_path: Path | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Create an architecture agent with injected external dependencies.

        Args:
            client: Configured asynchronous OpenAI SDK client.
            model: Approved GPT model identifier.
            prompt_loader: Prompt retrieval adapter; defaults to filesystem loading.
            prompt_path: Optional architecture prompt location for composition or tests.
            logger: Application logger supplied by the composition root.

        Raises:
            ValueError: If the model identifier is empty.
        """
        super().__init__(logger=logger)
        if not model.strip():
            raise ValueError("An OpenAI model identifier is required.")
        self._client = client
        self._model = model
        self._prompt_loader = prompt_loader or FilePromptLoader()
        self._prompt_path = prompt_path or Path(__file__).parent.parent / "prompts" / "architecture_prompt.md"
        self._prompt: str | None = None

    @property
    def name(self) -> str:
        """Return the stable identity used by workflow orchestration."""
        return "architecture_agent"

    async def initialize(self, state: AgentState) -> AgentState:
        """Load the version-controlled Principal Architect system prompt."""
        prompt = self._prompt_loader.load(self._prompt_path).strip()
        if not prompt:
            raise ValueError("Architecture prompt must not be empty.")
        self._prompt = prompt
        self._logger.debug("Architecture prompt initialized", extra={"execution_id": state.execution_id})
        return state

    async def execute(self, state: AgentState) -> AgentState:
        """Generate and persist a JSON-safe, validated architecture specification.

        Args:
            state: Shared workflow state containing requirements and plan artifacts.

        Returns:
            State populated with the validated architecture JSON representation and
            accumulated OpenAI usage metrics.

        Raises:
            RuntimeError: If the prompt has not been initialized.
            ValueError: If requirement or planning artifacts are missing.
            MalformedArchitectureResponseError: If the provider output is invalid.
        """
        if self._prompt is None:
            raise RuntimeError("ArchitectureAgent must be initialized before execution.")
        if not state.clarified_requirements:
            raise ValueError("Architecture generation requires clarified requirements.")
        if not state.tasks:
            raise ValueError("Architecture generation requires an approved engineering plan.")

        architecture_specification_schema = build_openai_json_schema(ArchitectureSpecification)
        architecture_input = json.dumps(
            {
                "project_name": state.project_name,
                "clarified_requirements": state.clarified_requirements,
                "tasks": [task.model_dump(mode="json") for task in state.tasks],
                "dependencies": [dependency.model_dump(mode="json") for dependency in state.dependencies],
                "assumptions": state.assumptions,
                "acceptance_criteria": state.acceptance_criteria,
                "architecture_specification_schema": architecture_specification_schema,
            },
            ensure_ascii=False,
        )
        response = await self._client.responses.create(
            model=self._model,
            input=[
                {"role": "system", "content": self._prompt},
                {"role": "user", "content": architecture_input},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "architecture_specification",
                    "strict": True,
                    "schema": architecture_specification_schema,
                }
            },
        )
        specification = self._parse_specification(response.output_text)
        metrics = state.metrics.model_copy(
            update={
                "total_input_tokens": state.metrics.total_input_tokens + self._usage_value(response, "input_tokens"),
                "total_output_tokens": state.metrics.total_output_tokens + self._usage_value(response, "output_tokens"),
            }
        )
        return state.model_copy(
            update={"architecture": specification.model_dump(mode="json"), "metrics": metrics},
            deep=True,
        )

    async def validate(self, state: AgentState) -> AgentState:
        """Re-validate the persisted architecture artifact against its Pydantic schema."""
        if not state.architecture:
            raise ValueError("Architecture generation produced no specification.")
        try:
            ArchitectureSpecification.model_validate(state.architecture)
        except ValidationError as error:
            raise MalformedArchitectureResponseError("Stored architecture violates ArchitectureSpecification.") from error
        return state

    async def retry(self, state: AgentState) -> AgentState:
        """Reload the prompt and perform one fresh architecture-generation attempt."""
        initialized_state = await self.initialize(state)
        return await self.execute(initialized_state)

    async def rollback(self, state: AgentState) -> AgentState:
        """Clear partial architecture output after a terminal execution failure."""
        return state.model_copy(update={"architecture": {}}, deep=True)

    async def report(self, state: AgentState) -> AgentState:
        """Emit a structured, non-sensitive architecture completion log."""
        self._logger.info(
            "Architecture generation completed",
            extra={
                "execution_id": state.execution_id,
                "status": state.execution_status.value,
                "architecture_generated": bool(state.architecture),
                "approval_required": state.approval_required,
            },
        )
        return state

    @staticmethod
    def _parse_specification(raw_output: str) -> ArchitectureSpecification:
        """Parse strict JSON into a validated immutable architecture specification."""
        if not raw_output or not raw_output.strip():
            raise MalformedArchitectureResponseError("OpenAI returned an empty architecture response.")
        try:
            return ArchitectureSpecification.model_validate_json(raw_output)
        except ValidationError as error:
            raise MalformedArchitectureResponseError("OpenAI returned malformed architecture JSON.") from error

    @staticmethod
    def _usage_value(response: Any, field_name: str) -> int:
        """Read a non-negative token-usage field defensively from an SDK response."""
        usage = getattr(response, "usage", None)
        value = getattr(usage, field_name, 0) if usage is not None else 0
        return value if isinstance(value, int) and value >= 0 else 0
