"""Dependency-aware execution of enterprise generated-file plans."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from threading import RLock

from pydantic import BaseModel, ConfigDict, Field

from agentic_software_engineer.codegen.dependency_resolver import DependencyResolver
from agentic_software_engineer.codegen.generic_generator import GenericCodeGenerator
from agentic_software_engineer.codegen.project_builder import ProjectBuilder, RollbackResult, WriteResult
from agentic_software_engineer.domain.entities.code_generation_plan import (
    CodeGenerationPlan,
    FileSpecification,
    GeneratedArtifact,
    GenerationReport,
    GenerationStatus,
)
from agentic_software_engineer.llm.client import LLMGenerationError
from agentic_software_engineer.validators.code_validator import CodeValidationResult, CodeValidator


class GenerationExecutorConfiguration(BaseModel):
    """Bounded concurrency settings for code-generation execution."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    max_concurrency: int = Field(default=4, ge=1, le=32, description="Maximum files generated concurrently within one batch.")


@dataclass(frozen=True, slots=True)
class _FileExecutionOutcome:
    """Internal immutable outcome from one planned file execution."""

    file_id: str
    status: GenerationStatus
    artifact: GeneratedArtifact | None
    validation_failures: tuple[str, ...]
    retry_count: int
    latency_ms: int
    write_count: int
    rollback_count: int
    reason: str | None = None


class GenerationExecutor:
    """Execute a code-generation plan with dependency, validation, and write gates.

    Independent files in each Kahn-resolved batch run concurrently under a
    semaphore. A failed file blocks only downstream files that depend on it;
    unrelated branches continue. The executor never logs prompts, generated
    content, artifact bodies, or secrets.
    """

    def __init__(
        self,
        generic_generator: GenericCodeGenerator,
        code_validator: CodeValidator,
        project_builder: ProjectBuilder,
        dependency_resolver: DependencyResolver,
        *,
        configuration: GenerationExecutorConfiguration | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Create an executor with injected generation, validation, storage, and graph dependencies."""
        self._generic_generator = generic_generator
        self._code_validator = code_validator
        self._project_builder = project_builder
        self._dependency_resolver = dependency_resolver
        self._configuration = configuration or GenerationExecutorConfiguration()
        self._logger = logger or logging.getLogger(__name__)
        self._execution_write_counts: dict[str, int] = {}
        self._rollback_lock = RLock()

    async def execute(
        self,
        *,
        plan: CodeGenerationPlan,
        architecture_context: str,
        project_context: str,
        execution_id: str,
        target_file_ids: set[str] | None = None,
        completed_artifacts: dict[str, GeneratedArtifact] | None = None,
    ) -> GenerationReport:
        """Generate, validate, and persist all reachable files in a dependency plan.

        Args:
            plan: Approved dependency-aware file generation plan.
            architecture_context: Approved architecture content supplied to file generation.
            project_context: Safe project conventions and relevant existing context.
            execution_id: Stable execution correlation identifier.
            target_file_ids: Optional failed-file IDs for a partial retry execution.
            completed_artifacts: Previously completed direct-dependency artifacts.

        Returns:
            Complete report with artifacts, failures, blocked files, retries, and
            timing/count telemetry.
        """
        start_time = datetime.now(UTC)
        started_at = perf_counter()
        batches = self._dependency_resolver.resolve(plan)
        semaphore = asyncio.Semaphore(self._configuration.max_concurrency)
        artifacts_by_id = dict(completed_artifacts or {})
        outcomes: dict[str, _FileExecutionOutcome] = {
            file_id: _FileExecutionOutcome(
                file_id=file_id,
                status=GenerationStatus.COMPLETED,
                artifact=artifact,
                validation_failures=(),
                retry_count=0,
                latency_ms=0,
                write_count=0,
                rollback_count=0,
            )
            for file_id, artifact in artifacts_by_id.items()
        }

        for batch_index, batch in enumerate(batches, start=1):
            self._logger.info(
                "Processing generation dependency batch",
                extra={"execution_id": execution_id, "batch_index": batch_index, "file_count": len(batch)},
            )
            selected_batch = [
                specification
                for specification in batch
                if target_file_ids is None or specification.id in target_file_ids
            ]
            batch_tasks = [
                self._process_file(
                    specification=specification,
                    artifacts_by_id=artifacts_by_id,
                    completed_outcomes=outcomes,
                    architecture_context=architecture_context,
                    project_context=project_context,
                    execution_id=execution_id,
                    semaphore=semaphore,
                )
                for specification in selected_batch
            ]
            batch_outcomes = await asyncio.gather(*batch_tasks)
            for outcome in batch_outcomes:
                outcomes[outcome.file_id] = outcome
                if outcome.artifact is not None and outcome.status is GenerationStatus.COMPLETED:
                    artifacts_by_id[outcome.file_id] = outcome.artifact

        end_time = datetime.now(UTC)
        duration_seconds = perf_counter() - started_at
        report = self._report(plan, execution_id, outcomes, start_time, end_time, duration_seconds)
        with self._rollback_lock:
            self._execution_write_counts[execution_id] = self._execution_write_counts.get(execution_id, 0) + report.write_count
        return report

    def rollback_execution(self, execution_id: str) -> list[RollbackResult]:
        """Roll back writes made by this executor for one execution identifier."""
        with self._rollback_lock:
            write_count = self._execution_write_counts.pop(execution_id, 0)
        results: list[RollbackResult] = []
        for _ in range(write_count):
            result = self._project_builder.rollback_latest()
            results.append(result)
            if not result.rolled_back:
                break
        return results

    async def _process_file(
        self,
        *,
        specification: FileSpecification,
        artifacts_by_id: dict[str, GeneratedArtifact],
        completed_outcomes: dict[str, _FileExecutionOutcome],
        architecture_context: str,
        project_context: str,
        execution_id: str,
        semaphore: asyncio.Semaphore,
    ) -> _FileExecutionOutcome:
        """Process one file, or block it when a direct dependency has not completed."""
        failed_dependencies = [
            dependency_id
            for dependency_id in specification.dependencies
            if completed_outcomes.get(dependency_id) is None
            or completed_outcomes[dependency_id].status is not GenerationStatus.COMPLETED
        ]
        if failed_dependencies:
            reason = f"Blocked by incomplete dependencies: {', '.join(sorted(failed_dependencies))}."
            self._logger.warning("Generation file blocked", extra={"execution_id": execution_id, "file_id": specification.id})
            return _FileExecutionOutcome(
                file_id=specification.id,
                status=GenerationStatus.BLOCKED,
                artifact=None,
                validation_failures=(),
                retry_count=0,
                latency_ms=0,
                write_count=0,
                rollback_count=0,
                reason=reason,
            )

        async with semaphore:
            return await self._generate_validate_write(
                specification=specification,
                artifacts_by_id=artifacts_by_id,
                architecture_context=architecture_context,
                project_context=project_context,
                execution_id=execution_id,
            )

    async def _generate_validate_write(
        self,
        *,
        specification: FileSpecification,
        artifacts_by_id: dict[str, GeneratedArtifact],
        architecture_context: str,
        project_context: str,
        execution_id: str,
    ) -> _FileExecutionOutcome:
        """Generate and validate one file, retrying only validation failures."""
        started_at = perf_counter()
        validation_failures: list[str] = []
        dependency_context = {
            dependency_id: artifacts_by_id[dependency_id].content
            for dependency_id in specification.dependencies
            if dependency_id in artifacts_by_id
        }
        for retry_number in range(specification.max_retries + 1):
            attempt_specification = specification.model_copy(update={"retry_count": retry_number})
            repair_context = project_context
            if validation_failures:
                repair_context = self._repair_context(project_context, validation_failures)
            try:
                artifact = await self._generic_generator.generate_file(
                    specification=attempt_specification,
                    architecture_context=architecture_context,
                    project_context=repair_context,
                    dependency_context=dependency_context,
                    execution_id=execution_id,
                )
            except LLMGenerationError:
                return self._failure_outcome(
                    specification.id,
                    retry_number,
                    started_at,
                    "Generation provider failed; executor does not retry non-validation failures.",
                )
            except Exception as error:
                self._logger.error(
                    "Generated artifact preparation failed",
                    extra={"execution_id": execution_id, "file_id": specification.id, "error_type": type(error).__name__},
                )
                return self._failure_outcome(
                    specification.id,
                    retry_number,
                    started_at,
                    "Generated artifact could not be prepared.",
                )

            validation = self._code_validator.validate(artifact, attempt_specification)
            if validation.valid:
                completed_artifact = artifact.model_copy(update={"validation_status": GenerationStatus.COMPLETED})
                try:
                    write_result = self._project_builder.write(completed_artifact, attempt_specification)
                except (OSError, ValueError) as error:
                    self._logger.error(
                        "Generated artifact write failed",
                        extra={"execution_id": execution_id, "file_id": specification.id, "error_type": type(error).__name__},
                    )
                    return self._failure_outcome(
                        specification.id,
                        retry_number,
                        started_at,
                        "Generated artifact could not be persisted.",
                        validation_failures,
                    )
                return self._write_outcome(
                    specification.id,
                    completed_artifact,
                    validation_failures,
                    retry_number,
                    started_at,
                    write_result,
                )

            validation_failures.extend(self._validation_messages(validation))
            self._logger.warning(
                "Generated file validation failed",
                extra={
                    "execution_id": execution_id,
                    "file_id": specification.id,
                    "attempt": retry_number + 1,
                    "blocking_rules": [issue.rule for issue in validation.issues if issue.blocking],
                },
            )

        return self._failure_outcome(
            specification.id,
            specification.max_retries,
            started_at,
            "Generated file exceeded validation retry limit.",
            validation_failures,
        )

    @staticmethod
    def _repair_context(project_context: str, validation_failures: list[str]) -> str:
        """Append validation-only repair instructions without adding unrelated source content."""
        errors = "\n".join(f"- {message}" for message in validation_failures[-20:])
        return f"{project_context}\n\nRepair the same file only. Previous validation findings:\n{errors}"

    @staticmethod
    def _validation_messages(validation: CodeValidationResult) -> list[str]:
        """Return concise blocking validation messages for report and repair context."""
        return [issue.message for issue in validation.issues if issue.blocking]

    def _write_outcome(
        self,
        file_id: str,
        artifact: GeneratedArtifact,
        validation_failures: list[str],
        retry_count: int,
        started_at: float,
        write_result: WriteResult,
    ) -> _FileExecutionOutcome:
        """Translate a persistence result into a stable per-file execution outcome."""
        latency_ms = int((perf_counter() - started_at) * 1_000)
        if write_result.written:
            return _FileExecutionOutcome(
                file_id=file_id,
                status=GenerationStatus.COMPLETED,
                artifact=artifact,
                validation_failures=tuple(validation_failures),
                retry_count=retry_count,
                latency_ms=latency_ms,
                write_count=1,
                rollback_count=0,
            )
        if write_result.action == "preserved":
            return _FileExecutionOutcome(
                file_id=file_id,
                status=GenerationStatus.SKIPPED,
                artifact=None,
                validation_failures=tuple(validation_failures),
                retry_count=retry_count,
                latency_ms=latency_ms,
                write_count=0,
                rollback_count=0,
                reason=write_result.message,
            )
        return self._failure_outcome(
            file_id,
            retry_count,
            started_at,
            write_result.message,
            validation_failures,
        )

    @staticmethod
    def _failure_outcome(
        file_id: str,
        retry_count: int,
        started_at: float,
        reason: str,
        validation_failures: list[str] | None = None,
    ) -> _FileExecutionOutcome:
        """Create a non-retryable failed-file outcome with safe telemetry."""
        return _FileExecutionOutcome(
            file_id=file_id,
            status=GenerationStatus.FAILED,
            artifact=None,
            validation_failures=tuple(validation_failures or ()),
            retry_count=retry_count,
            latency_ms=int((perf_counter() - started_at) * 1_000),
            write_count=0,
            rollback_count=0,
            reason=reason,
        )

    @staticmethod
    def _report(
        plan: CodeGenerationPlan,
        execution_id: str,
        outcomes: dict[str, _FileExecutionOutcome],
        start_time: datetime,
        end_time: datetime,
        duration_seconds: float,
    ) -> GenerationReport:
        """Aggregate per-file outcomes into the durable plan execution report."""
        generated = [outcome.artifact for outcome in outcomes.values() if outcome.status is GenerationStatus.COMPLETED and outcome.artifact is not None]
        skipped = sorted(outcome.file_id for outcome in outcomes.values() if outcome.status is GenerationStatus.SKIPPED)
        failed = sorted(outcome.file_id for outcome in outcomes.values() if outcome.status is GenerationStatus.FAILED)
        blocked = sorted(outcome.file_id for outcome in outcomes.values() if outcome.status is GenerationStatus.BLOCKED)
        validation_failures = [
            f"{outcome.file_id}: {message}"
            for outcome in outcomes.values()
            for message in outcome.validation_failures
        ]
        required_failed = any(
            outcome.status in {GenerationStatus.FAILED, GenerationStatus.BLOCKED}
            and next(specification for specification in plan.files if specification.id == outcome.file_id).required
            for outcome in outcomes.values()
        )
        status = GenerationStatus.FAILED if required_failed else GenerationStatus.COMPLETED
        return GenerationReport(
            execution_id=execution_id,
            generated_files=generated,
            skipped_files=skipped,
            failed_files=failed,
            blocked_files=blocked,
            validation_failures=validation_failures,
            retry_count=sum(outcome.retry_count for outcome in outcomes.values()),
            file_latencies_ms={outcome.file_id: outcome.latency_ms for outcome in outcomes.values()},
            write_count=sum(outcome.write_count for outcome in outcomes.values()),
            rollback_count=sum(outcome.rollback_count for outcome in outcomes.values()),
            start_time=start_time,
            end_time=end_time,
            duration_seconds=duration_seconds,
            status=status,
        )
