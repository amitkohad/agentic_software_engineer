"""Deterministic change-impact analysis for enterprise software projects."""

from __future__ import annotations

import ast
import re
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agentic_software_engineer.domain.entities.architecture_specification import ArchitectureSpecification


class RiskLevel(StrEnum):
    """Deterministic classification of change implementation risk."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AffectedFile(BaseModel):
    """A project file potentially impacted by the requested change."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, description="Project-relative file path.")
    reasons: list[str] = Field(min_length=1, description="Deterministic evidence for potential impact.")


class AffectedClass(BaseModel):
    """A Python class potentially impacted by the requested change."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, description="Class name.")
    file_path: str = Field(min_length=1, description="Project-relative source file path.")
    reasons: list[str] = Field(min_length=1, description="Deterministic evidence for potential impact.")


class AffectedApi(BaseModel):
    """An approved API operation potentially impacted by a requested change."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str = Field(min_length=1, description="Stable API operation identifier.")
    method: str = Field(min_length=1, description="HTTP method.")
    path: str = Field(min_length=1, description="Endpoint path.")
    reasons: list[str] = Field(min_length=1, description="Deterministic evidence for potential impact.")


class MigrationStrategy(BaseModel):
    """Recommended data or contract migration approach for the requested change."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    required: bool = Field(description="Whether a migration is indicated by deterministic evidence.")
    strategy: str = Field(min_length=1, description="Recommended migration approach.")
    steps: list[str] = Field(min_length=1, description="Ordered migration and verification steps.")


class ImpactAnalysis(BaseModel):
    """JSON-serializable enterprise impact-analysis result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requested_change: str = Field(min_length=1, description="Original requested change.")
    affected_files: list[AffectedFile] = Field(default_factory=list)
    affected_classes: list[AffectedClass] = Field(default_factory=list)
    affected_apis: list[AffectedApi] = Field(default_factory=list)
    migration_strategy: MigrationStrategy
    risk_level: RiskLevel
    risk_reasons: list[str] = Field(default_factory=list, description="Factors determining the risk level.")


class ImpactAnalyzer:
    """Analyze potential change impact using architecture and static project evidence.

    The analyzer never executes project code and does not use AI. It tokenizes
    the requested change, scans text files under the configured project root,
    parses Python classes with ``ast``, and correlates the results with declared
    architecture APIs, domain entities, modules, and database tables.
    """

    _IGNORED_DIRECTORIES = frozenset({".git", ".venv", "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache"})
    _RISK_KEYWORDS = {
        "security": 25,
        "authentication": 25,
        "authorization": 25,
        "permission": 20,
        "database": 20,
        "schema": 20,
        "migration": 20,
        "delete": 20,
        "remove": 15,
        "rename": 15,
        "breaking": 25,
        "deploy": 10,
        "production": 10,
    }

    def analyze(
        self,
        architecture: ArchitectureSpecification,
        existing_project: Path,
        requested_change: str,
    ) -> ImpactAnalysis:
        """Determine likely change impact from architecture and project source evidence.

        Args:
            architecture: Approved architecture specification for the project.
            existing_project: Root directory of the existing generated project.
            requested_change: Natural-language request describing the intended change.

        Returns:
            Structured, JSON-serializable impact analysis.

        Raises:
            ValueError: If the change is empty or project root does not exist.
        """
        if not requested_change.strip():
            raise ValueError("requested_change must not be empty.")
        project_root = existing_project.resolve()
        if not project_root.is_dir():
            raise ValueError("existing_project must be an existing directory.")

        change_tokens = self._tokens(requested_change)
        architecture_terms = self._architecture_terms(architecture)
        relevant_terms = change_tokens & architecture_terms
        affected_files = self._affected_files(project_root, change_tokens, relevant_terms)
        affected_classes = self._affected_classes(project_root, affected_files, change_tokens, relevant_terms)
        affected_apis = self._affected_apis(architecture, change_tokens, relevant_terms)
        migration_strategy = self._migration_strategy(architecture, change_tokens, relevant_terms)
        risk_level, risk_reasons = self._risk_level(
            requested_change,
            affected_files,
            affected_apis,
            migration_strategy,
        )
        return ImpactAnalysis(
            requested_change=requested_change,
            affected_files=affected_files,
            affected_classes=affected_classes,
            affected_apis=affected_apis,
            migration_strategy=migration_strategy,
            risk_level=risk_level,
            risk_reasons=risk_reasons,
        )

    def analyze_json(
        self,
        architecture: ArchitectureSpecification,
        existing_project: Path,
        requested_change: str,
    ) -> str:
        """Return the deterministic impact analysis as a JSON string for other agents."""
        return self.analyze(architecture, existing_project, requested_change).model_dump_json(indent=2)

    @staticmethod
    def _tokens(value: str) -> set[str]:
        """Normalize text into stable lowercase alphanumeric analysis tokens."""
        normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
        return {token.casefold() for token in re.findall(r"[A-Za-z][A-Za-z0-9_]*", normalized)}

    def _architecture_terms(self, architecture: ArchitectureSpecification) -> set[str]:
        """Collect matchable architecture vocabulary from declared contracts."""
        terms: set[str] = set()
        for module in architecture.modules:
            terms.update(self._tokens(" ".join([module.name, module.responsibility, *module.interfaces, *module.data_owned])))
        for entity in architecture.domain_entities:
            terms.update(self._tokens(" ".join([entity.name, entity.description, *entity.relationships])))
        for api in architecture.api_definitions:
            terms.update(self._tokens(" ".join([api.operation_id, api.path, api.summary])))
        if architecture.database_schema is not None:
            for table in architecture.database_schema.tables:
                terms.update(self._tokens(" ".join([table.name, table.purpose])))
        return terms

    def _affected_files(
        self,
        project_root: Path,
        change_tokens: set[str],
        relevant_terms: set[str],
    ) -> list[AffectedFile]:
        """Find text files whose content or paths intersect change and architecture terms."""
        affected: list[AffectedFile] = []
        for path in self._project_files(project_root):
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            file_tokens = self._tokens(f"{path.relative_to(project_root)} {content}")
            matches = sorted((change_tokens | relevant_terms) & file_tokens)
            if matches:
                affected.append(
                    AffectedFile(
                        path=path.relative_to(project_root).as_posix(),
                        reasons=[f"Matches change or architecture terms: {', '.join(matches[:10])}."],
                    )
                )
        return affected

    def _affected_classes(
        self,
        project_root: Path,
        affected_files: list[AffectedFile],
        change_tokens: set[str],
        relevant_terms: set[str],
    ) -> list[AffectedClass]:
        """Parse affected Python files and return classes whose names intersect change vocabulary."""
        affected_classes: list[AffectedClass] = []
        for affected_file in affected_files:
            path = project_root / affected_file.path
            if path.suffix != ".py":
                continue
            try:
                module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(module):
                if not isinstance(node, ast.ClassDef):
                    continue
                class_tokens = self._tokens(node.name)
                matches = sorted(class_tokens & (change_tokens | relevant_terms))
                if matches:
                    affected_classes.append(
                        AffectedClass(
                            name=node.name,
                            file_path=affected_file.path,
                            reasons=[f"Class name matches analysis terms: {', '.join(matches)}."],
                        )
                    )
        return affected_classes

    def _affected_apis(
        self,
        architecture: ArchitectureSpecification,
        change_tokens: set[str],
        relevant_terms: set[str],
    ) -> list[AffectedApi]:
        """Correlate requested-change tokens with approved API operation metadata."""
        affected_apis: list[AffectedApi] = []
        for api in architecture.api_definitions:
            api_tokens = self._tokens(f"{api.operation_id} {api.path} {api.summary}")
            matches = sorted(api_tokens & (change_tokens | relevant_terms))
            if matches:
                affected_apis.append(
                    AffectedApi(
                        operation_id=api.operation_id,
                        method=api.method,
                        path=api.path,
                        reasons=[f"API contract matches analysis terms: {', '.join(matches)}."],
                    )
                )
        return affected_apis

    def _migration_strategy(
        self,
        architecture: ArchitectureSpecification,
        change_tokens: set[str],
        relevant_terms: set[str],
    ) -> MigrationStrategy:
        """Determine whether database migration safeguards are indicated by change evidence."""
        database_terms = {"database", "schema", "migration", "table", "column", "index"}
        database_names = {
            token
            for table in (architecture.database_schema.tables if architecture.database_schema else [])
            for token in self._tokens(table.name)
        }
        required = bool((change_tokens | relevant_terms) & (database_terms | database_names))
        if required:
            return MigrationStrategy(
                required=True,
                strategy="expand-contract migration with backward-compatible rollout",
                steps=[
                    "Add backward-compatible schema changes through a versioned migration.",
                    "Deploy application support for both old and new representations.",
                    "Backfill and verify data with observable, resumable jobs.",
                    "Remove legacy schema only after compatibility and rollback windows close.",
                ],
            )
        return MigrationStrategy(
            required=False,
            strategy="no data migration indicated by available change evidence",
            steps=["Confirm that no persistent data contract changes are required before implementation."],
        )

    def _risk_level(
        self,
        requested_change: str,
        affected_files: list[AffectedFile],
        affected_apis: list[AffectedApi],
        migration_strategy: MigrationStrategy,
    ) -> tuple[RiskLevel, list[str]]:
        """Calculate a stable risk level from explicit keyword and impact signals."""
        tokens = self._tokens(requested_change)
        score = min(len(affected_files), 15) + min(len(affected_apis) * 5, 15)
        reasons = [f"Potentially affected files: {len(affected_files)}.", f"Potentially affected APIs: {len(affected_apis)}."]
        for keyword, weight in self._RISK_KEYWORDS.items():
            if keyword in tokens:
                score += weight
                reasons.append(f"Risk keyword detected: '{keyword}'.")
        if migration_strategy.required:
            score += 20
            reasons.append("Database migration safeguards are indicated.")
        if score >= 55:
            return RiskLevel.CRITICAL, reasons
        if score >= 35:
            return RiskLevel.HIGH, reasons
        if score >= 15:
            return RiskLevel.MEDIUM, reasons
        return RiskLevel.LOW, reasons

    def _project_files(self, project_root: Path) -> list[Path]:
        """Return sorted text-candidate files while excluding dependency and cache directories."""
        files = [
            path
            for path in project_root.rglob("*")
            if path.is_file() and not any(part in self._IGNORED_DIRECTORIES for part in path.relative_to(project_root).parts)
        ]
        return sorted(files, key=lambda path: path.as_posix())
