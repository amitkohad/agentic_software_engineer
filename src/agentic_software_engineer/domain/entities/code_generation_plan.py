"""Strict Pydantic contracts for enterprise AI code-generation plans."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from agentic_software_engineer.domain.entities.architecture_specification import ArchitectureModel


class OverwritePolicy(StrEnum):
    """Allowed behaviors when a generated file already exists."""

    NEVER = "never"
    ALWAYS = "always"
    IF_GENERATED = "if_generated"
    REQUIRE_APPROVAL = "require_approval"


class DirectorySpecification(ArchitectureModel):
    """A project directory and the purpose it serves in the generated solution."""

    path: str = Field(min_length=1, description="Project-relative directory path.")
    purpose: str = Field(min_length=1, description="Responsibility assigned to the directory.")


class ProjectStructure(ArchitectureModel):
    """Top-level repository layout and source-organization conventions."""

    root_directory: str = Field(min_length=1, description="Root directory for generated project assets.")
    directories: list[DirectorySpecification] = Field(min_length=1, description="Required project directories.")
    conventions: list[str] = Field(default_factory=list, description="Naming and layout conventions to apply.")


class ModuleDependency(ArchitectureModel):
    """A directed implementation dependency between generated modules."""

    target_module: str = Field(min_length=1, description="Stable identifier of the depended-on module.")
    dependency_type: str = Field(min_length=1, description="Relationship type, such as import or runtime.")
    rationale: str = Field(min_length=1, description="Reason the dependency is required.")


class ModuleGenerationSpecification(ArchitectureModel):
    """A cohesive source module to be generated from the approved architecture."""

    module_id: str = Field(min_length=1, description="Stable, machine-readable module identifier.")
    name: str = Field(min_length=1, description="Human-readable module name.")
    purpose: str = Field(min_length=1, description="Primary module responsibility.")
    source_directory: str = Field(min_length=1, description="Directory that owns the module source.")
    dependencies: list[ModuleDependency] = Field(default_factory=list, description="Outbound module dependencies.")
    public_interfaces: list[str] = Field(default_factory=list, description="Interfaces or contracts exposed by the module.")


class FileSpecification(ArchitectureModel):
    """A single file to generate, with constraints suitable for an AI coding agent."""

    path: str = Field(min_length=1, description="Project-relative file path.")
    purpose: str = Field(min_length=1, description="Responsibility of the file.")
    dependencies: list[str] = Field(default_factory=list, description="Files, modules, or contracts required first.")
    generation_prompt: str = Field(min_length=1, description="Scoped instruction supplied to the code-generation agent.")
    validation_rules: list[str] = Field(min_length=1, description="Rules used to validate generated content.")
    overwrite_policy: OverwritePolicy = Field(description="Allowed replacement behavior for an existing file.")


class GenerationDependency(ArchitectureModel):
    """A directed ordering edge between two planned generated artifacts."""

    predecessor: str = Field(min_length=1, description="Artifact path or identifier that must be generated first.")
    successor: str = Field(min_length=1, description="Artifact path or identifier blocked by the predecessor.")
    required: bool = Field(default=True, description="Whether this dependency blocks generation.")
    rationale: str = Field(min_length=1, description="Reason the ordering dependency exists.")


class ExternalPackage(ArchitectureModel):
    """A third-party package approved for the generated project."""

    name: str = Field(min_length=1, description="Package name.")
    version_constraint: str = Field(min_length=1, description="Approved version or version range.")
    purpose: str = Field(min_length=1, description="Capability provided by the package.")
    package_manager: str = Field(min_length=1, description="Package manager responsible for installation.")


class EnvironmentVariable(ArchitectureModel):
    """A typed runtime configuration variable required by the generated project."""

    name: str = Field(min_length=1, description="Environment variable name.")
    description: str = Field(min_length=1, description="Purpose and expected value semantics.")
    required: bool = Field(description="Whether the application can run without the variable.")
    secret: bool = Field(default=False, description="Whether the value must be managed as a secret.")
    default_value: str | None = Field(default=None, description="Safe non-secret default when allowed.")


class CommandSpecification(ArchitectureModel):
    """A reproducible project command and its intended operating context."""

    name: str = Field(min_length=1, description="Short command identifier.")
    command: str = Field(min_length=1, description="Command text to execute.")
    purpose: str = Field(min_length=1, description="Outcome expected from command execution.")
    working_directory: str | None = Field(default=None, description="Optional project-relative execution directory.")


class DeploymentFileSpecification(ArchitectureModel):
    """A deployment or infrastructure artifact to generate alongside application code."""

    path: str = Field(min_length=1, description="Project-relative deployment artifact path.")
    format: str = Field(min_length=1, description="Artifact format, such as YAML, Terraform, or Dockerfile.")
    purpose: str = Field(min_length=1, description="Deployment responsibility of the artifact.")
    environments: list[str] = Field(min_length=1, description="Target environments that consume the artifact.")
    validation_rules: list[str] = Field(min_length=1, description="Required validation rules for the artifact.")


class CodeGenerationPlan(ArchitectureModel):
    """Complete, immutable plan for safe enterprise AI code generation."""

    project_name: str = Field(min_length=1, description="Name of the target generated project.")
    architecture_reference: str = Field(min_length=1, description="Identifier, path, or version of the approved architecture artifact.")
    target_language: str = Field(min_length=1, description="Primary implementation language.")
    framework: str = Field(min_length=1, description="Target application framework or runtime.")
    project_structure: ProjectStructure = Field(description="Repository layout and source conventions.")
    modules: list[ModuleGenerationSpecification] = Field(min_length=1, description="Planned implementation modules.")
    files: list[FileSpecification] = Field(min_length=1, description="Ordered-independent inventory of source files to generate.")
    generation_order: list[str] = Field(min_length=1, description="Ordered file paths or artifact identifiers for generation.")
    dependencies: list[GenerationDependency] = Field(default_factory=list, description="Directed generation dependency graph.")
    external_packages: list[ExternalPackage] = Field(default_factory=list, description="Approved external packages.")
    environment_variables: list[EnvironmentVariable] = Field(default_factory=list, description="Runtime configuration variables.")
    build_commands: list[CommandSpecification] = Field(default_factory=list, description="Build and setup commands.")
    test_commands: list[CommandSpecification] = Field(default_factory=list, description="Test and quality-verification commands.")
    deployment_files: list[DeploymentFileSpecification] = Field(default_factory=list, description="Deployment and infrastructure artifacts.")
