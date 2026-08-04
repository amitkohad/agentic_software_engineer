"""Requirement-analysis agent backed by the OpenAI Responses API."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError

from agentic_software_engineer.agents.base import BaseAgent, build_openai_json_schema
from agentic_software_engineer.agents.prompt_loader import FilePromptLoader, PromptLoader
from agentic_software_engineer.orchestrator.state import AgenticSDLCState as AgentState


class RequirementAnalysis(BaseModel):
    """Strict JSON contract produced by the requirement-analysis model call."""

    clarified_requirements: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


REQUIREMENT_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "clarified_requirements": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["clarified_requirements", "assumptions", "acceptance_criteria"],
    "additionalProperties": False,
}


class MalformedRequirementResponseError(ValueError):
    """Raised when a model response cannot satisfy the requirement JSON contract."""


class RequirementAgent(BaseAgent):
    """Transform a user requirement into structured, reviewable requirement state.

    The OpenAI client, model identifier, prompt loader, and logger are injected
    to keep the agent independently testable and to avoid coupling the domain
    workflow to environment configuration. The base lifecycle owns recovery and
    status transitions; this class owns only requirement analysis.
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
        """Create a requirement agent with all external dependencies injected.

        Args:
            client: Configured asynchronous OpenAI SDK client.
            model: Approved GPT model identifier.
            prompt_loader: Prompt retrieval implementation; defaults to local files.
            prompt_path: Optional requirement prompt location for composition or tests.
            logger: Application logger supplied by the dependency-injection root.

        Raises:
            ValueError: If ``model`` is empty.
        """
        super().__init__(logger=logger)
        if not model.strip():
            raise ValueError("An OpenAI model identifier is required.")

        self._client = client
        self._model = model
        self._prompt_loader = prompt_loader or FilePromptLoader()
        self._prompt_path = prompt_path or Path(__file__).parent.parent / "prompts" / "requirement_prompt.md"
        self._prompt: str | None = None

    @property
    def name(self) -> str:
        """Return the stable identity used by orchestration and audit logs."""
        return "requirement_agent"

    async def initialize(self, state: AgentState) -> AgentState:
        """Load the version-controlled prompt needed for requirement analysis.

        Args:
            state: Current shared workflow state.

        Returns:
            The unchanged state after prompt initialization.

        Raises:
            FileNotFoundError: If the configured prompt asset cannot be found.
            ValueError: If the prompt asset is empty.
        """
        prompt = self._prompt_loader.load(self._prompt_path).strip()
        if not prompt:
            raise ValueError("Requirement prompt must not be empty.")
        self._prompt = prompt
        self._logger.debug("Requirement prompt initialized", extra={"execution_id": state.execution_id})
        return state

    async def execute(self, state: AgentState) -> AgentState:
        """Call OpenAI and merge the validated requirement analysis into state.

        Args:
            state: Workflow state containing ``user_requirement``.

        Returns:
            A state copy populated with clarified requirements, assumptions,
            acceptance criteria, and OpenAI usage metrics.

        Raises:
            RuntimeError: If initialization has not loaded the prompt.
            MalformedRequirementResponseError: If the model output is empty or
                violates the required JSON schema.
        """
        if self._prompt is None:
            raise RuntimeError("RequirementAgent must be initialized before execution.")

        response = await self._client.responses.create(
            model=self._model,
            input=[
                {"role": "system", "content": self._prompt},
                {"role": "user", "content": state.user_requirement},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "requirement_analysis",
                    "strict": True,
                    "schema": build_openai_json_schema(REQUIREMENT_ANALYSIS_SCHEMA),
                }
            },
        )

        analysis = self._parse_analysis(response.output_text)
        metrics = state.metrics.model_copy(
            update={
                "total_input_tokens": state.metrics.total_input_tokens + self._usage_value(response, "input_tokens"),
                "total_output_tokens": state.metrics.total_output_tokens + self._usage_value(response, "output_tokens"),
            }
        )
        return state.model_copy(
            update={
                "clarified_requirements": analysis.clarified_requirements,
                "assumptions": analysis.assumptions,
                "acceptance_criteria": analysis.acceptance_criteria,
                "metrics": metrics,
            },
            deep=True,
        )

    async def validate(self, state: AgentState) -> AgentState:
        """Confirm that the requirement output contains testable acceptance criteria.

        Args:
            state: State produced by this agent's execution attempt.

        Returns:
            The validated workflow state.

        Raises:
            ValueError: If no clarified requirements or acceptance criteria exist.
        """
        if not state.clarified_requirements:
            raise ValueError("Requirement analysis produced no clarified requirements.")
        if not state.acceptance_criteria:
            raise ValueError("Requirement analysis produced no acceptance criteria.")
        return state

    async def retry(self, state: AgentState) -> AgentState:
        """Reload the prompt and run one fresh requirement-analysis attempt.

        Args:
            state: Failure state prepared by the base lifecycle template.

        Returns:
            State from the new OpenAI requirement-analysis attempt.
        """
        initialized_state = await self.initialize(state)
        return await self.execute(initialized_state)

    async def rollback(self, state: AgentState) -> AgentState:
        """Remove incomplete requirement artifacts after terminal failure.

        Args:
            state: Failed state passed by the base lifecycle template.

        Returns:
            State with partial requirement-analysis artifacts cleared.
        """
        return state.model_copy(
            update={
                "clarified_requirements": [],
                "assumptions": [],
                "acceptance_criteria": [],
            },
            deep=True,
        )

    async def report(self, state: AgentState) -> AgentState:
        """Emit a completion log while leaving durable reporting to the orchestrator.

        Args:
            state: Completed, failed, or approval-paused workflow state.

        Returns:
            The unchanged state for LangGraph persistence.
        """
        self._logger.info(
            "Requirement analysis completed",
            extra={
                "execution_id": state.execution_id,
                "status": state.execution_status.value,
                "approval_required": state.approval_required,
            },
        )
        return state

    @staticmethod
    def _parse_analysis(raw_output: str) -> RequirementAnalysis:
        """Parse and validate strict JSON emitted by the OpenAI response.

        Args:
            raw_output: Model response text expected to contain exactly one JSON object.

        Returns:
            Validated requirement analysis.

        Raises:
            MalformedRequirementResponseError: If output is blank or invalid.
        """
        if not raw_output or not raw_output.strip():
            raise MalformedRequirementResponseError("OpenAI returned an empty requirement analysis.")
        try:
            return RequirementAnalysis.model_validate_json(raw_output)
        except ValidationError as error:
            raise MalformedRequirementResponseError("OpenAI returned malformed requirement JSON.") from error

    @staticmethod
    def _usage_value(response: Any, field_name: str) -> int:
        """Read a non-negative usage field defensively from an SDK response."""
        usage = getattr(response, "usage", None)
        value = getattr(usage, field_name, 0) if usage is not None else 0
        return value if isinstance(value, int) and value >= 0 else 0
