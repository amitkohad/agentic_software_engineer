"""Plan-and-executor orchestration agent for enterprise code generation."""

from __future__ import annotations

import logging
import json
from typing import Protocol

from agentic_software_engineer.agents.base import BaseAgent
from agentic_software_engineer.application.ports.state_store import StateStore
from agentic_software_engineer.codegen.generation_executor import GenerationExecutor
from agentic_software_engineer.domain.entities.architecture_specification import ArchitectureSpecification
from agentic_software_engineer.domain.entities.code_generation_plan import (
    CodeGenerationPlan,
    GeneratedArtifact,
    GenerationReport,
    GenerationStatus,
    OverwritePolicy,
)
from agentic_software_engineer.orchestrator.state import AgenticSDLCState as AgentState
from agentic_software_engineer.orchestrator.state import GeneratedFile


class CodePlanningError(ValueError):
    """Raised when a valid code-generation plan cannot be created or loaded."""


class GenerationExecutionError(RuntimeError):
    """Raised when durable generation execution state is missing or inconsistent."""


class CodePlanGenerator(Protocol):
    """Create a validated code-generation plan from approved architecture state."""

    async def create_plan(self, state: AgentState, architecture: ArchitectureSpecification) -> CodeGenerationPlan:
        """Return a complete approved code-generation plan for the requested project."""


class CodingAgent(BaseAgent):
    """Coordinate code planning, generation execution, state persistence, and recovery.

    This agent does not depend on individual file generators. It delegates all
    file-level concerns to ``GenerationExecutor`` and relies on ``StateStore``
    for durable state snapshots between workflow nodes and approval gates.
    """

    def __init__(
        self,
        code_plan_generator: CodePlanGenerator,
        generation_executor: GenerationExecutor,
        state_store: StateStore,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        """Create a coding agent with plan, execution, and persistence dependencies injected."""
        super().__init__(logger=logger)
        self._code_plan_generator = code_plan_generator
        self._generation_executor = generation_executor
        self._state_store = state_store

    @property
    def name(self) -> str:
        """Return the stable workflow identity for code-generation orchestration."""
        return "coding_agent"

    async def initialize(self, state: AgentState) -> AgentState:
        """Verify that architecture and project-root prerequisites are available."""
        if not state.architecture:
            raise CodePlanningError("Coding requires an approved architecture specification.")
        ArchitectureSpecification.model_validate(state.architecture)
        if not state.project_root or not state.project_root.strip():
            raise CodePlanningError("Coding requires a configured project root.")
        return self._persist(state)

    async def execute(self, state: AgentState) -> AgentState:
        """Create or load a plan, enforce approval gates, and execute reachable files."""
        architecture = ArchitectureSpecification.model_validate(state.architecture)
        plan = await self._load_or_create_plan(state, architecture)
        planned_state = self._persist(
            state.model_copy(update={"code_generation_plan": plan.model_dump(mode="json")}, deep=True)
        )
        approval_files = [
            file_specification.id
            for file_specification in plan.files
            if (file_specification.requires_human_approval
            or file_specification.overwrite_policy is OverwritePolicy.REQUIRE_APPROVAL)
            and file_specification.id not in state.approved_file_ids
        ]
        if approval_files:
            self._logger.info(
                "Code generation paused for approval",
                extra={"execution_id": state.execution_id, "approval_file_count": len(approval_files)},
            )
            return self._persist(
                planned_state.model_copy(
                    update={"approval_required": True, "pending_approval_files": sorted(approval_files)},
                    deep=True,
                )
            )

        report = await self._generation_executor.execute(
            plan=plan,
            architecture_context=self._architecture_context(state),
            project_context=self._project_context(state),
            execution_id=state.execution_id,
        )
        return self._persist(self._apply_report(planned_state, report))

    async def validate(self, state: AgentState) -> AgentState:
        """Confirm mandatory completion, resolved validation, and build/test commands."""
        if state.approval_required:
            return state
        plan = self._plan_from_state(state)
        report = self._report_from_state(state)
        completed_ids = {artifact.file_id for artifact in report.generated_files}
        missing_required = [
            file_specification.id
            for file_specification in plan.files
            if file_specification.required and file_specification.id not in completed_ids
        ]
        if missing_required:
            raise GenerationExecutionError(f"Mandatory files were not completed: {', '.join(sorted(missing_required))}.")
        if report.failed_files or report.blocked_files:
            raise GenerationExecutionError("Generation report contains unresolved failed or blocked files.")
        if not plan.build_commands:
            raise GenerationExecutionError("Code-generation plan must define at least one build command.")
        if not plan.test_commands:
            raise GenerationExecutionError("Code-generation plan must define at least one test command.")
        return state

    async def retry(self, state: AgentState) -> AgentState:
        """Retry only failed or validation-failed files, preserving completed artifacts."""
        plan = self._plan_from_state(state)
        report = self._report_from_state(state)
        failed_ids = set(report.failed_files)
        failed_ids.update(
            artifact.file_id
            for artifact in report.generated_files
            if artifact.validation_status is GenerationStatus.VALIDATION_FAILED
        )
        if not failed_ids:
            return state
        retry_report = await self._generation_executor.execute(
            plan=plan,
            architecture_context=self._architecture_context(state),
            project_context=self._project_context(state),
            execution_id=state.execution_id,
            target_file_ids=failed_ids,
            completed_artifacts={artifact.file_id: artifact for artifact in report.generated_files},
        )
        return self._persist(self._apply_report(state, retry_report, preserve_completed=True))

    async def rollback(self, state: AgentState) -> AgentState:
        """Ask the executor to restore files written in the current execution."""
        rollback_results = self._generation_executor.rollback_execution(state.execution_id)
        affected_hashes = {
            result.path: result.restored_hash
            for result in rollback_results
            if result.path is not None and result.restored_hash is not None
        }
        rollback_reason = "Code-generation rollback requested by workflow recovery."
        metrics = state.metrics.model_copy(
            update={
                "custom": {
                    **state.metrics.custom,
                    "rollback_files": len(rollback_results),
                    "rollback_hashes": str(affected_hashes),
                }
            }
        )
        return self._persist(state.model_copy(update={"rollback_reason": rollback_reason, "metrics": metrics}, deep=True))

    async def report(self, state: AgentState) -> AgentState:
        """Log a safe GenerationReport summary retained in the durable workflow state."""
        if state.generation_report:
            report = self._report_from_state(state)
            self._logger.info(
                "Code-generation report finalized",
                extra={
                    "execution_id": state.execution_id,
                    "status": report.status.value,
                    "generated_files": len(report.generated_files),
                    "failed_files": len(report.failed_files),
                    "blocked_files": len(report.blocked_files),
                },
            )
        return self._persist(state)

    async def _load_or_create_plan(self, state: AgentState, architecture: ArchitectureSpecification) -> CodeGenerationPlan:
        """Load a durable plan when available or create one from approved architecture."""
        if state.code_generation_plan:
            try:
                return CodeGenerationPlan.model_validate(state.code_generation_plan)
            except ValueError as error:
                raise CodePlanningError("Persisted code-generation plan is invalid.") from error
        return await self._code_plan_generator.create_plan(state, architecture)

    @staticmethod
    def _project_context(state: AgentState) -> str:
        """Build a safe, metadata-only project context for code-generation execution."""
        return f"Project name: {state.project_name}\nProject root: {state.project_root or ''}"

    @staticmethod
    def _architecture_context(state: AgentState) -> str:
        """Serialize the validated architecture artifact for the generation boundary."""
        return json.dumps(state.architecture, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _plan_from_state(state: AgentState) -> CodeGenerationPlan:
        """Load the validated durable code-generation plan from workflow state."""
        if not state.code_generation_plan:
            raise GenerationExecutionError("Code-generation plan is missing from workflow state.")
        return CodeGenerationPlan.model_validate(state.code_generation_plan)

    @staticmethod
    def _report_from_state(state: AgentState) -> GenerationReport:
        """Load the validated durable generation report from workflow state."""
        if not state.generation_report:
            raise GenerationExecutionError("Generation report is missing from workflow state.")
        return GenerationReport.model_validate(state.generation_report)

    @staticmethod
    def _apply_report(state: AgentState, report: GenerationReport, *, preserve_completed: bool = False) -> AgentState:
        """Persist report metadata, generated-file inventory, and aggregate metrics in state."""
        previous_files = state.generated_files if preserve_completed else []
        generated_files = [
            GeneratedFile(
                path=artifact.path,
                operation="generated",
                checksum=artifact.content_hash,
                metadata={
                    "file_id": artifact.file_id,
                    "model": artifact.model,
                    "prompt_version": artifact.prompt_version,
                    "validation_status": artifact.validation_status.value,
                },
            )
            for artifact in report.generated_files
        ]
        metrics = state.metrics.model_copy(
            update={
                "total_tool_calls": state.metrics.total_tool_calls + report.write_count,
                "custom": {
                    **state.metrics.custom,
                    "generation_duration_seconds": report.duration_seconds,
                    "generation_validation_failures": len(report.validation_failures),
                    "generation_blocked_files": len(report.blocked_files),
                    "generation_write_count": report.write_count,
                    "generation_rollback_count": report.rollback_count,
                },
            }
        )
        merged_files = {
            str(file.metadata.get("file_id", file.path)): file
            for file in [*previous_files, *generated_files]
        }
        return state.model_copy(
            update={
                "generation_report": report.model_dump(mode="json"),
                "generated_files": list(merged_files.values()),
                "metrics": metrics,
                "retry_count": state.retry_count + report.retry_count,
                "approval_required": False,
                "pending_approval_files": [],
            },
            deep=True,
        )

    def _persist(self, state: AgentState) -> AgentState:
        """Save a state snapshot through the injected state-store boundary."""
        return self._state_store.save_execution_state(state)
