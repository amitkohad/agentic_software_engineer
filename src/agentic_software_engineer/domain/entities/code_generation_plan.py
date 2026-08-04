"""Strict Pydantic contracts for enterprise agentic code-generation planning."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePath

from pydantic import Field, model_validator

from agentic_software_engineer.domain.entities.architecture_specification import ArchitectureModel


class FileType(StrEnum):
    """Functional category of a generated project file."""

    API = "api"
    DOMAIN = "domain"
    REPOSITORY = "repository"
    CONFIGURATION = "configuration"
    TEST = "test"
    DOCUMENTATION = "documentation"
    MIGRATION = "migration"
    INFRASTRUCTURE = "infrastructure"


class OverwritePolicy(StrEnum):
    """Allowed action when generation targets an existing file."""

    NEVER = "never"
    CREATE_ONLY = "create_only"
    REPLACE = "replace"
    MERGE = "merge"
    REQUIRE_APPROVAL = "require_approval"


class GenerationStatus(StrEnum):
    """Lifecycle state of a planned file or overall generation execution."""

    PENDING = "pending"
    BLOCKED = "blocked"
    GENERATING = "generating"
    GENERATED = "generated"
    VALIDATION_FAILED = "validation_failed"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ExternalPackage(ArchitectureModel):
    """An approved third-party package required by the generated project."""

    name: str = Field(min_length=1, description="Package name.")
    version_constraint: str = Field(min_length=1, description="Approved version or version range.")
    purpose: str = Field(min_length=1, description="Capability supplied by the package.")
    package_manager: str = Field(min_length=1, description="Package manager that installs the package.")


class EnvironmentVariable(ArchitectureModel):
    """A typed runtime configuration variable required by the generated project."""

    name: str = Field(min_length=1, pattern=r"^[A-Z][A-Z0-9_]*$", description="Uppercase environment variable name.")
    description: str = Field(min_length=1, description="Purpose and expected value semantics.")
    required: bool = Field(description="Whether the application requires the variable to run.")
    secret: bool = Field(default=False, description="Whether the value must be stored as a secret.")
    default_value: str | None = Field(default=None, description="Safe non-secret default value when permitted.")

    @model_validator(mode="after")
    def validate_secret_default(self) -> EnvironmentVariable:
        """Prevent accidental secret defaults in version-controlled plans."""
        if self.secret and self.default_value is not None:
            raise ValueError("Secret environment variables must not define a default value.")
        return self


class CommandSpecification(ArchitectureModel):
    """A reproducible build, test, or operational command."""

    name: str = Field(min_length=1, description="Stable command identifier.")
    command: str = Field(min_length=1, description="Command text to execute.")
    purpose: str = Field(min_length=1, description="Expected command outcome.")
    working_directory: str | None = Field(default=None, description="Optional project-relative execution directory.")


class FileSpecification(ArchitectureModel):
    """Complete generation contract for one project file."""

    id: str = Field(min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$", description="Stable file identifier.")
    path: str = Field(min_length=1, description="Project-relative target path.")
    file_type: FileType = Field(description="Functional category of the target file.")
    purpose: str = Field(min_length=1, description="Responsibility of the file.")
    dependencies: list[str] = Field(default_factory=list, description="Identifiers of files that must be available first.")
    symbols_to_define: list[str] = Field(default_factory=list, description="Public symbols expected in the generated file.")
    symbols_to_import: list[str] = Field(default_factory=list, description="Symbols required from other files or packages.")
    validation_rules: list[str] = Field(default_factory=list, description="Required validation rules for generated content.")
    overwrite_policy: OverwritePolicy = Field(description="Permitted existing-file behavior.")
    requires_human_approval: bool = Field(default=False, description="Whether human approval is required before generation.")
    required: bool = Field(default=True, description="Whether a generation failure blocks the overall execution outcome.")
    existing_file_context: str | None = Field(default=None, description="Optional bounded context from an existing target file.")
    generation_status: GenerationStatus = Field(default=GenerationStatus.PENDING, description="Current file generation lifecycle status.")
    retry_count: int = Field(default=0, ge=0, description="Number of failed generation attempts so far.")
    max_retries: int = Field(default=1, ge=0, description="Maximum permitted retry attempts.")

    @model_validator(mode="after")
    def validate_file_contract(self) -> FileSpecification:
        """Enforce safe paths, unique dependency references, and approval consistency."""
        path = PurePath(self.path)
        if path.is_absolute() or ".." in path.parts or self.path in {".", ""}:
            raise ValueError("FileSpecification.path must be a safe project-relative path.")
        if self.id in self.dependencies:
            raise ValueError("A file cannot depend on itself.")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("File dependencies must be unique.")
        if self.retry_count > self.max_retries:
            raise ValueError("retry_count must not exceed max_retries.")
        if self.overwrite_policy is OverwritePolicy.REQUIRE_APPROVAL and not self.requires_human_approval:
            raise ValueError("require_approval overwrite policy requires human approval.")
        return self


class CodeGenerationPlan(ArchitectureModel):
    """Versioned, dependency-aware plan for enterprise code-generation execution."""

    project_name: str = Field(min_length=1, description="Name of the generated software project.")
    project_root: str = Field(min_length=1, description="Safe root directory relative to the generation workspace.")
    target_language: str = Field(min_length=1, description="Primary implementation language.")
    framework: str = Field(min_length=1, description="Primary application framework or runtime.")
    files: list[FileSpecification] = Field(min_length=1, description="All files governed by this generation plan.")
    external_packages: list[ExternalPackage] = Field(default_factory=list, description="Approved third-party dependencies.")
    environment_variables: list[EnvironmentVariable] = Field(default_factory=list, description="Runtime configuration contract.")
    build_commands: list[CommandSpecification] = Field(default_factory=list, description="Build and setup commands.")
    test_commands: list[CommandSpecification] = Field(default_factory=list, description="Test and quality-verification commands.")
    architecture_version: str = Field(min_length=1, description="Approved architecture artifact version used for planning.")
    plan_version: str = Field(min_length=1, description="Version of this immutable generation plan.")

    @model_validator(mode="after")
    def validate_plan_integrity(self) -> CodeGenerationPlan:
        """Validate root path safety, unique file identity, and dependency references."""
        root = PurePath(self.project_root)
        if root.is_absolute() or ".." in root.parts:
            raise ValueError("project_root must be a safe workspace-relative path.")
        file_ids = [file.id for file in self.files]
        file_paths = [file.path for file in self.files]
        if len(set(file_ids)) != len(file_ids):
            raise ValueError("CodeGenerationPlan file IDs must be unique.")
        if len(set(file_paths)) != len(file_paths):
            raise ValueError("CodeGenerationPlan file paths must be unique.")
        known_ids = set(file_ids)
        for file in self.files:
            unknown_dependencies = set(file.dependencies) - known_ids
            if unknown_dependencies:
                raise ValueError(f"File '{file.id}' references unknown dependencies: {sorted(unknown_dependencies)}.")
        pending_dependencies = {file.id: set(file.dependencies) for file in self.files}
        resolved: set[str] = set()
        while pending_dependencies:
            ready = {file_id for file_id, dependencies in pending_dependencies.items() if dependencies <= resolved}
            if not ready:
                raise ValueError("CodeGenerationPlan file dependencies must be acyclic.")
            resolved.update(ready)
            for file_id in ready:
                pending_dependencies.pop(file_id)
        return self

    @property
    def generation_order(self) -> list[str]:
        """Return a deterministic topological ordering of planned file paths."""
        pending_dependencies = {file.id: set(file.dependencies) for file in self.files}
        files_by_id = {file.id: file for file in self.files}
        resolved: set[str] = set()
        order: list[str] = []
        while pending_dependencies:
            ready = sorted(file_id for file_id, dependencies in pending_dependencies.items() if dependencies <= resolved)
            for file_id in ready:
                order.append(files_by_id[file_id].path)
                resolved.add(file_id)
                pending_dependencies.pop(file_id)
        return order


class GeneratedArtifact(ArchitectureModel):
    """Immutable evidence for one generated file artifact and its validation outcome."""

    file_id: str = Field(min_length=1, description="Identifier of the source FileSpecification.")
    path: str = Field(min_length=1, description="Project-relative generated file path.")
    content: str = Field(description="Exact generated file content retained for audit and handoff.")
    content_hash: str = Field(min_length=64, max_length=128, pattern=r"^[A-Fa-f0-9]+$", description="Cryptographic content hash in hexadecimal.")
    generated_at: datetime = Field(description="UTC timestamp of artifact generation.")
    model: str = Field(min_length=1, description="Model or deterministic generator identity.")
    prompt_version: str = Field(min_length=1, description="Prompt or template version used for generation.")
    validation_status: GenerationStatus = Field(description="Validation lifecycle outcome for this artifact.")
    validation_errors: list[str] = Field(default_factory=list, description="Validation failures recorded for this artifact.")
    attempt_number: int = Field(ge=1, description="One-based generation attempt number.")

    @model_validator(mode="after")
    def validate_artifact_outcome(self) -> GeneratedArtifact:
        """Require explicit validation errors whenever validation has failed."""
        if len(self.content_hash) not in {64, 128}:
            raise ValueError("content_hash must be a SHA-256 or SHA-512 hexadecimal digest.")
        if self.validation_status is GenerationStatus.VALIDATION_FAILED and not self.validation_errors:
            raise ValueError("validation_failed artifacts must include validation_errors.")
        return self


class GenerationReport(ArchitectureModel):
    """Durable execution summary for a complete code-generation run."""

    execution_id: str = Field(min_length=1, description="Unique code-generation execution identifier.")
    generated_files: list[GeneratedArtifact] = Field(default_factory=list, description="Artifacts generated during the execution.")
    skipped_files: list[str] = Field(default_factory=list, description="File identifiers skipped by policy or partial scope.")
    failed_files: list[str] = Field(default_factory=list, description="File identifiers that failed generation.")
    blocked_files: list[str] = Field(default_factory=list, description="File identifiers blocked by failed dependencies.")
    validation_failures: list[str] = Field(default_factory=list, description="Aggregate validation failure descriptions.")
    retry_count: int = Field(default=0, ge=0, description="Aggregate retry count for this execution.")
    file_latencies_ms: dict[str, int] = Field(default_factory=dict, description="Per-file generation and validation latency in milliseconds.")
    write_count: int = Field(default=0, ge=0, description="Count of successfully persisted generated artifacts.")
    rollback_count: int = Field(default=0, ge=0, description="Count of rollback operations performed during execution.")
    start_time: datetime = Field(description="UTC execution start time.")
    end_time: datetime = Field(description="UTC execution end time.")
    duration_seconds: float = Field(ge=0, description="Measured execution duration in seconds.")
    status: GenerationStatus = Field(description="Terminal or current execution lifecycle status.")

    @model_validator(mode="after")
    def validate_timing(self) -> GenerationReport:
        """Ensure execution timing is chronologically valid."""
        if self.end_time < self.start_time:
            raise ValueError("end_time must not precede start_time.")
        generated_ids = {artifact.file_id for artifact in self.generated_files}
        if generated_ids & set(self.skipped_files) or generated_ids & set(self.failed_files):
            raise ValueError("A file cannot be both generated and skipped or failed in one report.")
        return self
