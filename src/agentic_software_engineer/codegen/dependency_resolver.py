"""Deterministic dependency batching for code-generation plans."""

from __future__ import annotations

from collections import defaultdict

from agentic_software_engineer.domain.entities.code_generation_plan import CodeGenerationPlan, FileSpecification


class MissingDependencyError(ValueError):
    """Raised when a planned file references a file identifier that does not exist."""


class DependencyCycleError(ValueError):
    """Raised when planned file dependencies cannot be scheduled acyclically."""


class DependencyResolver:
    """Create deterministic parallel generation batches with Kahn's algorithm.

    Each resolved batch contains files whose direct dependencies were satisfied by
    earlier batches. File identifiers are sorted within each batch, ensuring a
    stable plan across executions while preserving safe parallelism.
    """

    def resolve(self, plan: CodeGenerationPlan) -> list[list[FileSpecification]]:
        """Return dependency-safe generation batches for the supplied plan.

        Args:
            plan: Approved code-generation plan containing files keyed by ID.

        Returns:
            Ordered batches of independent file specifications. Files in the same
            inner list may execute in parallel.

        Raises:
            MissingDependencyError: If a dependency ID is absent from the plan.
            DependencyCycleError: If a self-dependency or directed cycle exists.
        """
        files_by_id = {file_specification.id: file_specification for file_specification in plan.files}
        if len(files_by_id) != len(plan.files):
            raise DependencyCycleError("Code-generation plan contains duplicate file IDs.")

        in_degree: dict[str, int] = {file_id: 0 for file_id in files_by_id}
        dependents: dict[str, set[str]] = defaultdict(set)
        for file_specification in plan.files:
            for dependency_id in file_specification.dependencies:
                if dependency_id not in files_by_id:
                    raise MissingDependencyError(
                        f"File '{file_specification.id}' references missing dependency '{dependency_id}'."
                    )
                if dependency_id == file_specification.id:
                    raise DependencyCycleError(f"File '{file_specification.id}' depends on itself.")
                if file_specification.id not in dependents[dependency_id]:
                    dependents[dependency_id].add(file_specification.id)
                    in_degree[file_specification.id] += 1

        batches: list[list[FileSpecification]] = []
        ready_ids = sorted(file_id for file_id, degree in in_degree.items() if degree == 0)
        resolved_count = 0
        while ready_ids:
            current_ids = ready_ids
            batches.append([files_by_id[file_id] for file_id in current_ids])
            resolved_count += len(current_ids)

            next_ready: list[str] = []
            for file_id in current_ids:
                for dependent_id in sorted(dependents[file_id]):
                    in_degree[dependent_id] -= 1
                    if in_degree[dependent_id] == 0:
                        next_ready.append(dependent_id)
            ready_ids = sorted(next_ready)

        if resolved_count != len(files_by_id):
            unresolved_ids = sorted(file_id for file_id, degree in in_degree.items() if degree > 0)
            raise DependencyCycleError(
                f"Code-generation plan contains a dependency cycle involving: {', '.join(unresolved_ids)}."
            )
        return batches
