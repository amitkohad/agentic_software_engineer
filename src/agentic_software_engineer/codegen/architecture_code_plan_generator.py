"""Deterministic conversion of an approved architecture into a generation plan."""

from __future__ import annotations

import re

from agentic_software_engineer.domain.entities.architecture_specification import ArchitectureSpecification
from agentic_software_engineer.domain.entities.code_generation_plan import (
    CodeGenerationPlan,
    CommandSpecification,
    ExternalPackage,
    FileSpecification,
    FileType,
    OverwritePolicy,
)
from agentic_software_engineer.orchestrator.state import AgenticSDLCState as AgentState


class ArchitectureCodePlanGenerator:
    """Create a safe, deterministic Python/FastAPI file plan from architecture data.

    This adapter intentionally derives file contracts without asking another
    model. The injected file generator remains responsible for implementing the
    contents of every planned file under the approved architecture.
    """

    async def create_plan(self, state: AgentState, architecture: ArchitectureSpecification) -> CodeGenerationPlan:
        """Return the baseline Clean Architecture plan for the approved project."""
        if not state.project_root:
            raise ValueError("A project root is required before planning generated files.")

        package_name = self._package_name(architecture.project_name)
        files: list[FileSpecification] = [
            FileSpecification(
                id="application-entrypoint",
                path=f"src/{package_name}/main.py",
                file_type=FileType.API,
                purpose="FastAPI application composition root and route registration.",
                symbols_to_define=["app"],
                validation_rules=["Define a FastAPI application named app."],
                overwrite_policy=OverwritePolicy.CREATE_ONLY,
            )
        ]
        domain_ids: list[str] = []
        for entity in architecture.domain_entities:
            entity_slug = self._slug(entity.name)
            file_id = f"domain-{entity_slug}"
            domain_ids.append(file_id)
            files.append(
                FileSpecification(
                    id=file_id,
                    path=f"src/{package_name}/domain/{entity_slug}.py",
                    file_type=FileType.DOMAIN,
                    purpose=entity.description,
                    symbols_to_define=[entity.name],
                    validation_rules=["Implement the domain entity and its stated invariants."],
                    overwrite_policy=OverwritePolicy.CREATE_ONLY,
                )
            )

        files.extend(
            [
                FileSpecification(
                    id="api-routes",
                    path=f"src/{package_name}/api/routes.py",
                    file_type=FileType.API,
                    purpose="REST API routes defined by the approved architecture.",
                    dependencies=domain_ids,
                    validation_rules=["Implement only approved REST routes and dependency injection wiring."],
                    overwrite_policy=OverwritePolicy.CREATE_ONLY,
                ),
                FileSpecification(
                    id="api-route-tests",
                    path=f"tests/test_api_routes.py",
                    file_type=FileType.TEST,
                    purpose="Integration tests for approved REST API operations.",
                    dependencies=["application-entrypoint", "api-routes"],
                    validation_rules=["Include executable pytest tests."],
                    overwrite_policy=OverwritePolicy.CREATE_ONLY,
                ),
                FileSpecification(
                    id="project-readme",
                    path="README.md",
                    file_type=FileType.DOCUMENTATION,
                    purpose="Developer documentation for the generated application.",
                    dependencies=["application-entrypoint", "api-routes"],
                    validation_rules=["Include a Markdown heading and local run instructions."],
                    overwrite_policy=OverwritePolicy.CREATE_ONLY,
                ),
            ]
        )
        return CodeGenerationPlan(
            project_name=architecture.project_name,
            # The plan is portable and therefore records a workspace-relative
            # root. The absolute, approved write boundary remains solely in
            # AgentState.project_root and ProjectBuilder.
            project_root=".",
            target_language="Python 3.12",
            framework="FastAPI",
            files=files,
            external_packages=[
                ExternalPackage(name="fastapi", version_constraint=">=0.110", purpose="REST API framework", package_manager="pip"),
                ExternalPackage(name="pydantic", version_constraint=">=2.0", purpose="Data validation", package_manager="pip"),
                ExternalPackage(name="pytest", version_constraint=">=8.0", purpose="Test execution", package_manager="pip"),
            ],
            build_commands=[
                CommandSpecification(name="compile", command="python -m compileall src", purpose="Verify Python syntax."),
            ],
            test_commands=[
                CommandSpecification(name="test", command="python -m pytest", purpose="Run generated API tests."),
            ],
            architecture_version=state.architecture_version or "1",
            plan_version="1",
        )

    @staticmethod
    def _slug(value: str) -> str:
        """Return a filesystem-safe lowercase identifier from an architecture name."""
        normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", value).casefold()
        normalized = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
        return normalized or "entity"

    @classmethod
    def _package_name(cls, project_name: str) -> str:
        """Return a valid import package identifier derived from project name."""
        package_name = cls._slug(project_name)
        return f"project_{package_name}" if package_name[:1].isdigit() else package_name
