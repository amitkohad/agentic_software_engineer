"""Plan-driven orchestration agent for enterprise AI code generation."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from agentic_software_engineer.agents.base import BaseAgent
from agentic_software_engineer.domain.entities.architecture_specification import ArchitectureSpecification
from agentic_software_engineer.domain.entities.code_generation_plan import (
    CodeGenerationPlan,
    FileSpecification,
    OverwritePolicy,
)
from agentic_software_engineer.orchestrator.state import AgenticSDLCState as AgentState
from agentic_software_engineer.orchestrator.state import GeneratedFile
from agentic_software_engineer.tools.project_workspace import ProjectWorkspace


class CodeGenerationPlanner(Protocol):
    """Create one validated generation plan from approved workflow artifacts."""

    async def create_plan(self, state: AgentState, architecture: ArchitectureSpecification) -> CodeGenerationPlan:
        """Return the complete plan to be executed by specialized generators."""


class SpecializedFileGenerator(Protocol):
    """Generate a bounded class of files from a validated file specification."""

    def can_generate(self, file_specification: FileSpecification) -> bool:
        """Return whether this generator owns the specified file type or responsibility."""

    async def generate(
        self,
        file_specification: FileSpecification,
        plan: CodeGenerationPlan,
        state: AgentState,
        existing_content: str | None,
    ) -> str:
        """Return complete content for exactly one approved file specification."""


class CodingAgent(BaseAgent):
    """Coordinate plan-driven file generation without per-file direct LLM calls.

    The agent delegates plan creation and individual files to injected specialized
    components. This separation allows different generators for domain code,
    API adapters, tests, configuration, and deployment files while maintaining a
    single dependency-aware execution and persistence policy.
    """

    def __init__(
        self,
        planner: CodeGenerationPlanner,
        generators: Sequence[SpecializedFileGenerator],
        workspace: ProjectWorkspace,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        """Create the coding agent with all planning, generation, and I/O dependencies injected."""
        super().__init__(logger=logger)
        if not generators:
            raise ValueError("CodingAgent requires at least one specialized file generator.")
        self._planner = planner
        self._generators = tuple(generators)
        self._workspace = workspace

    @property
    def name(self) -> str:
        """Return the stable orchestration identity for this agent."""
        return "coding_agent"

    async def initialize(self, state: AgentState) -> AgentState:
        """Confirm an approved architecture exists before creating a generation plan."""
        if not state.architecture:
            raise ValueError("Code generation requires an approved architecture specification.")
        ArchitectureSpecification.model_validate(state.architecture)
        return state

    async def execute(self, state: AgentState) -> AgentState:
        """Create a plan, select eligible files, delegate generation, and persist results."""
        architecture = ArchitectureSpecification.model_validate(state.architecture)
        plan = await self._planner.create_plan(state, architecture)
        selected_files = self._select_files(plan, state.regeneration_targets)
        conflict_paths = self._approval_conflicts(selected_files)

        if conflict_paths:
            self._logger.warning(
                "Code generation requires overwrite approval",
                extra={"execution_id": state.execution_id, "paths": conflict_paths},
            )
            return self._with_plan_and_metrics(
                state,
                plan,
                generated_files=[],
                generated_count=0,
                skipped_count=0,
                approval_required=True,
            )

        generated_files: list[GeneratedFile] = []
        skipped_count = 0
        for file_specification in selected_files:
            if self._should_skip(file_specification):
                skipped_count += 1
                continue

            existing_content = (
                self._workspace.read_text(file_specification.path)
                if self._workspace.exists(file_specification.path)
                else None
            )
            generator = self._generator_for(file_specification)
            content = await generator.generate(file_specification, plan, state, existing_content)
            if not content.strip():
                raise ValueError(f"Generator returned empty content for '{file_specification.path}'.")
            self._workspace.write_text(file_specification.path, content)
            generated_files.append(
                GeneratedFile(
                    path=file_specification.path,
                    operation="updated" if existing_content is not None else "created",
                    metadata={"generator": generator.__class__.__name__},
                )
            )

        return self._with_plan_and_metrics(
            state,
            plan,
            generated_files=generated_files,
            generated_count=len(generated_files),
            skipped_count=skipped_count,
            approval_required=False,
        )

    async def validate(self, state: AgentState) -> AgentState:
        """Validate the persisted plan and generated-file inventory before reporting."""
        if not state.code_generation_plan:
            raise ValueError("Code generation produced no CodeGenerationPlan.")
        plan = CodeGenerationPlan.model_validate(state.code_generation_plan)
        planned_paths = {file_specification.path for file_specification in plan.files}
        if len(planned_paths) != len(plan.files):
            raise ValueError("CodeGenerationPlan contains duplicate file paths.")
        if not set(plan.generation_order).issubset(planned_paths):
            raise ValueError("CodeGenerationPlan generation order references unknown files.")
        if not state.approval_required:
            missing_files = [file.path for file in state.generated_files if not self._workspace.exists(file.path)]
            if missing_files:
                raise ValueError(f"Generated files are missing from workspace: {', '.join(missing_files)}")
        return state

    async def retry(self, state: AgentState) -> AgentState:
        """Repeat plan-driven generation once using the same injected collaborators."""
        initialized_state = await self.initialize(state)
        return await self.execute(initialized_state)

    async def rollback(self, state: AgentState) -> AgentState:
        """Preserve files for audit while clearing incomplete generation-plan state."""
        return state.model_copy(update={"code_generation_plan": {}}, deep=True)

    async def report(self, state: AgentState) -> AgentState:
        """Emit a structured operational summary without exposing generated source content."""
        self._logger.info(
            "Code generation completed",
            extra={
                "execution_id": state.execution_id,
                "status": state.execution_status.value,
                "generated_files": len(state.generated_files),
                "approval_required": state.approval_required,
            },
        )
        return state

    def _select_files(self, plan: CodeGenerationPlan, regeneration_targets: Sequence[str]) -> list[FileSpecification]:
        """Return files in approved generation order, optionally limited to partial targets."""
        files_by_path = {file_specification.path: file_specification for file_specification in plan.files}
        ordered_paths = plan.generation_order
        targets = set(regeneration_targets)
        if targets and not targets.issubset(files_by_path):
            unknown_paths = ", ".join(sorted(targets - files_by_path.keys()))
            raise ValueError(f"Partial regeneration references unknown files: {unknown_paths}")
        return [files_by_path[path] for path in ordered_paths if not targets or path in targets]

    def _approval_conflicts(self, file_specifications: Sequence[FileSpecification]) -> list[str]:
        """Return existing paths that require human approval before replacement."""
        return [
            file_specification.path
            for file_specification in file_specifications
            if file_specification.overwrite_policy is OverwritePolicy.REQUIRE_APPROVAL
            and self._workspace.exists(file_specification.path)
        ]

    def _should_skip(self, file_specification: FileSpecification) -> bool:
        """Apply deterministic overwrite policy before invoking a specialized generator."""
        if not self._workspace.exists(file_specification.path):
            return False
        if file_specification.overwrite_policy is OverwritePolicy.NEVER:
            return True
        return (
            file_specification.overwrite_policy is OverwritePolicy.IF_GENERATED
            and not self._workspace.is_generated(file_specification.path)
        )

    def _generator_for(self, file_specification: FileSpecification) -> SpecializedFileGenerator:
        """Select the first injected specialized generator that owns the file specification."""
        for generator in self._generators:
            if generator.can_generate(file_specification):
                return generator
        raise ValueError(f"No specialized generator can generate '{file_specification.path}'.")

    @staticmethod
    def _with_plan_and_metrics(
        state: AgentState,
        plan: CodeGenerationPlan,
        *,
        generated_files: Sequence[GeneratedFile],
        generated_count: int,
        skipped_count: int,
        approval_required: bool,
    ) -> AgentState:
        """Return a state copy containing the durable plan, files, and generation metrics."""
        custom_metrics = dict(state.metrics.custom)
        custom_metrics.update(
            {
                "files_generated": generated_count,
                "files_skipped": skipped_count,
                "plan_file_count": len(plan.files),
            }
        )
        metrics = state.metrics.model_copy(
            update={
                "total_tool_calls": state.metrics.total_tool_calls + generated_count,
                "custom": custom_metrics,
            }
        )
        return state.model_copy(
            update={
                "code_generation_plan": plan.model_dump(mode="json"),
                "generated_files": [*state.generated_files, *generated_files],
                "metrics": metrics,
                "approval_required": approval_required,
            },
            deep=True,
        )
