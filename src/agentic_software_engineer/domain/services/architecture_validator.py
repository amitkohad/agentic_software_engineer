"""Deterministic quality validation for enterprise architecture specifications."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agentic_software_engineer.domain.entities.architecture_specification import ArchitectureSpecification


class ArchitectureValidationReport(BaseModel):
    """Structured, reproducible outcome from architecture quality validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    warnings: list[str] = Field(default_factory=list, description="Non-blocking architecture gaps or risks.")
    errors: list[str] = Field(default_factory=list, description="Blocking enterprise architecture deficiencies.")
    recommendations: list[str] = Field(default_factory=list, description="Concrete actions that improve the specification.")
    score: int = Field(ge=0, le=100, description="Deterministic completeness score from zero to one hundred.")


class ArchitectureValidator:
    """Validate architecture completeness using explicit, deterministic rules.

    Scores start at 100. Each error deducts 15 points and each warning deducts
    5 points, with a lower bound of zero. Recommendations do not affect the
    score. Rule ordering is stable, making results suitable for CI quality gates.
    """

    _ERROR_DEDUCTION = 15
    _WARNING_DEDUCTION = 5

    def validate(self, specification: ArchitectureSpecification) -> ArchitectureValidationReport:
        """Inspect an architecture specification and return a quality report.

        Args:
            specification: Fully parsed enterprise architecture specification.

        Returns:
            Deterministic warnings, errors, recommendations, and score.
        """
        warnings: list[str] = []
        errors: list[str] = []
        recommendations: list[str] = []

        self._validate_apis(specification, warnings, errors, recommendations)
        self._validate_database(specification, warnings, errors, recommendations)
        self._validate_security(specification, warnings, errors, recommendations)
        self._validate_observability(specification, warnings, errors, recommendations)
        self._validate_scalability(specification, warnings, errors, recommendations)
        self._validate_deployment(specification, warnings, errors, recommendations)
        self._validate_assumptions(specification, warnings, recommendations)

        score = max(0, 100 - (len(errors) * self._ERROR_DEDUCTION) - (len(warnings) * self._WARNING_DEDUCTION))
        return ArchitectureValidationReport(
            warnings=warnings,
            errors=errors,
            recommendations=recommendations,
            score=score,
        )

    @staticmethod
    def _validate_apis(
        specification: ArchitectureSpecification,
        warnings: list[str],
        errors: list[str],
        recommendations: list[str],
    ) -> None:
        """Validate API operation coverage and minimum contract documentation."""
        if not specification.api_definitions:
            warnings.append("No API definitions are documented.")
            recommendations.append("Document API contracts or record why the architecture exposes no APIs.")
            return

        for api in specification.api_definitions:
            if not api.responses:
                errors.append(f"API '{api.operation_id}' has no documented responses.")
            if api.authentication_required and not api.authorization_policy:
                warnings.append(f"Authenticated API '{api.operation_id}' has no authorization policy.")
                recommendations.append(f"Define a least-privilege authorization policy for '{api.operation_id}'.")

    @staticmethod
    def _validate_database(
        specification: ArchitectureSpecification,
        warnings: list[str],
        errors: list[str],
        recommendations: list[str],
    ) -> None:
        """Validate database design completeness and key data integrity controls."""
        schema = specification.database_schema
        if schema is None:
            warnings.append("No database schema is documented.")
            recommendations.append("Document persistent storage or explicitly confirm that the system is stateless.")
            return
        if not schema.tables:
            warnings.append("Database schema contains no persistent tables or collections.")
        for table in schema.tables:
            if not any(column.primary_key for column in table.columns):
                errors.append(f"Database table '{table.name}' has no primary key.")
            if not table.indexes:
                warnings.append(f"Database table '{table.name}' has no documented indexes.")
                recommendations.append(f"Review access patterns and document required indexes for '{table.name}'.")

    @staticmethod
    def _validate_security(
        specification: ArchitectureSpecification,
        warnings: list[str],
        errors: list[str],
        recommendations: list[str],
    ) -> None:
        """Validate baseline security control coverage using declared categories."""
        if not specification.security_controls:
            errors.append("No security controls are documented.")
            recommendations.append("Document identity, authorization, encryption, input validation, and audit controls.")
            return

        categories = {control.category.casefold() for control in specification.security_controls}
        required_categories = {
            "identity": "identity/authentication",
            "authorization": "authorization",
            "encryption": "encryption",
            "audit": "audit logging",
        }
        for category, display_name in required_categories.items():
            if category not in categories:
                warnings.append(f"Security controls do not include {display_name} coverage.")
                recommendations.append(f"Add a documented security control for {display_name}.")

    @staticmethod
    def _validate_observability(
        specification: ArchitectureSpecification,
        warnings: list[str],
        errors: list[str],
        recommendations: list[str],
    ) -> None:
        """Validate logging, metrics, tracing, and alerting coverage."""
        observability = specification.observability
        if observability is None:
            errors.append("No observability specification is documented.")
            recommendations.append("Define structured logging, metrics, tracing, alerting, and dashboards.")
            return
        if not observability.metrics:
            warnings.append("Observability specification defines no metrics.")
        if not observability.alerting_rules:
            warnings.append("Observability specification defines no alerting rules.")
        if not observability.dashboard_requirements:
            recommendations.append("Define operational dashboards for service health, latency, errors, and capacity.")

    @staticmethod
    def _validate_scalability(
        specification: ArchitectureSpecification,
        warnings: list[str],
        errors: list[str],
        recommendations: list[str],
    ) -> None:
        """Validate capacity planning and horizontal scaling considerations."""
        scalability = specification.scalability
        if scalability is None:
            warnings.append("No scalability specification is documented.")
            recommendations.append("Document expected load, performance targets, capacity limits, and scaling strategy.")
            return
        if not scalability.performance_targets:
            warnings.append("Scalability specification defines no measurable performance targets.")
        if not scalability.capacity_limits:
            recommendations.append("Document known capacity limits and saturation behavior.")

    @staticmethod
    def _validate_deployment(
        specification: ArchitectureSpecification,
        warnings: list[str],
        errors: list[str],
        recommendations: list[str],
    ) -> None:
        """Validate deployment topology, release controls, and recovery coverage."""
        deployment = specification.deployment_architecture
        if deployment is None:
            errors.append("No deployment architecture is documented.")
            recommendations.append("Define environments, workloads, release strategy, and disaster recovery.")
            return
        if not deployment.components:
            errors.append("Deployment architecture contains no deployable components.")
        if not deployment.regions:
            errors.append("Deployment architecture contains no target regions or zones.")

    @staticmethod
    def _validate_assumptions(
        specification: ArchitectureSpecification,
        warnings: list[str],
        recommendations: list[str],
    ) -> None:
        """Validate that design uncertainty has been explicitly recorded."""
        if not specification.assumptions:
            warnings.append("No architecture assumptions are documented.")
            recommendations.append("Record material product, integration, operational, and compliance assumptions.")
