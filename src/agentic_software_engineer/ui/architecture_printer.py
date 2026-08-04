"""Rich rendering for enterprise architecture specifications."""

from __future__ import annotations

from rich.box import ROUNDED
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agentic_software_engineer.domain.entities.architecture_specification import ArchitectureSpecification


class ArchitecturePrinter:
    """Render an ``ArchitectureSpecification`` as readable Rich terminal output.

    The console is injected to keep output transport configurable and to make the
    renderer straightforward to test with a recording Rich console.
    """

    def __init__(self, console: Console | None = None) -> None:
        """Create a printer using the supplied Rich console or the default console."""
        self._console = console or Console()

    def print(self, specification: ArchitectureSpecification) -> None:
        """Render all major architecture views in an ordered terminal report."""
        self._console.print(
            Panel.fit(
                Text(specification.project_name, style="bold cyan"),
                title="Enterprise Architecture Specification",
                subtitle=specification.business_goal,
                border_style="cyan",
            )
        )
        self._console.print(self._architecture_style_panel(specification))
        self._console.print(self._technology_stack_table(specification))
        self._console.print(self._modules_table(specification))
        self._console.print(self._api_table(specification))
        self._console.print(self._database_table(specification))
        self._console.print(self._security_controls_table(specification))
        self._console.print(self._scalability_panel(specification))
        self._console.print(self._deployment_view(specification))
        self._console.print(self._tradeoffs_table(specification))

    @staticmethod
    def _architecture_style_panel(specification: ArchitectureSpecification) -> Panel:
        """Create a concise architecture-style panel."""
        return Panel(
            specification.architecture_style,
            title="Architecture Style",
            border_style="magenta",
            expand=False,
        )

    @staticmethod
    def _technology_stack_table(specification: ArchitectureSpecification) -> Table:
        """Render selected technologies and their architecture responsibilities."""
        table = Table(title="Technology Stack", box=ROUNDED, header_style="bold green")
        table.add_column("Technology", style="cyan")
        table.add_column("Category")
        table.add_column("Version")
        table.add_column("Purpose")
        for technology in specification.technology_stack:
            table.add_row(technology.name, technology.category, technology.version or "—", technology.purpose)
        return table

    @staticmethod
    def _modules_table(specification: ArchitectureSpecification) -> Table:
        """Render module ownership, interfaces, and dependency information."""
        table = Table(title="Modules", box=ROUNDED, header_style="bold green")
        table.add_column("Module", style="cyan")
        table.add_column("Responsibility")
        table.add_column("Interfaces")
        table.add_column("Dependencies")
        for module in specification.modules:
            dependencies = "\n".join(dependency.target_module for dependency in module.dependencies) or "—"
            table.add_row(
                module.name,
                module.responsibility,
                "\n".join(module.interfaces) or "—",
                dependencies,
            )
        return table

    @staticmethod
    def _api_table(specification: ArchitectureSpecification) -> Table:
        """Render REST operations with authentication and response coverage."""
        table = Table(title="REST APIs", box=ROUNDED, header_style="bold green")
        table.add_column("Method", style="bold cyan")
        table.add_column("Path")
        table.add_column("Operation")
        table.add_column("Auth")
        table.add_column("Responses")
        for api in specification.api_definitions:
            responses = ", ".join(str(response.status_code) for response in api.responses) or "—"
            table.add_row(
                api.method.upper(),
                api.path,
                api.summary,
                api.authorization_policy or ("Required" if api.authentication_required else "Public"),
                responses,
            )
        if not specification.api_definitions:
            table.add_row("—", "—", "No REST APIs documented", "—", "—")
        return table

    @staticmethod
    def _database_table(specification: ArchitectureSpecification) -> Table:
        """Render persistent data objects, columns, and index coverage."""
        table = Table(title="Database Tables", box=ROUNDED, header_style="bold green")
        table.add_column("Table", style="cyan")
        table.add_column("Purpose")
        table.add_column("Primary Key")
        table.add_column("Indexes")
        schema = specification.database_schema
        if schema is None:
            table.add_row("—", "No database schema documented", "—", "—")
            return table
        for database_table in schema.tables:
            primary_keys = ", ".join(column.name for column in database_table.columns if column.primary_key) or "—"
            indexes = ", ".join(index.name for index in database_table.indexes) or "—"
            table.add_row(database_table.name, database_table.purpose, primary_keys, indexes)
        if not schema.tables:
            table.add_row("—", "No tables or collections documented", "—", "—")
        return table

    @staticmethod
    def _security_controls_table(specification: ArchitectureSpecification) -> Table:
        """Render enterprise security controls and their verification approach."""
        table = Table(title="Security Controls", box=ROUNDED, header_style="bold green")
        table.add_column("Control", style="cyan")
        table.add_column("Category")
        table.add_column("Description")
        table.add_column("Verification")
        for control in specification.security_controls:
            table.add_row(control.control_id, control.category, control.description, control.verification_method)
        if not specification.security_controls:
            table.add_row("—", "—", "No security controls documented", "—")
        return table

    @staticmethod
    def _scalability_panel(specification: ArchitectureSpecification) -> Panel:
        """Render capacity and performance planning as a compact panel."""
        scalability = specification.scalability
        if scalability is None:
            return Panel("No scalability specification documented.", title="Scalability", border_style="yellow")
        content = Group(
            Text.assemble(("Expected load: ", "bold"), scalability.expected_load),
            Text.assemble(("Scaling strategy: ", "bold"), scalability.scaling_strategy),
            Text.assemble(("Performance targets: ", "bold"), ", ".join(scalability.performance_targets) or "—"),
            Text.assemble(("Capacity limits: ", "bold"), ", ".join(scalability.capacity_limits) or "—"),
        )
        return Panel(content, title="Scalability", border_style="yellow")

    @staticmethod
    def _deployment_view(specification: ArchitectureSpecification) -> Panel | Table:
        """Render deployment strategy and deployed component topology."""
        deployment = specification.deployment_architecture
        if deployment is None:
            return Panel("No deployment architecture documented.", title="Deployment", border_style="yellow")
        table = Table(box=ROUNDED, header_style="bold green")
        table.add_column("Component", style="cyan")
        table.add_column("Type")
        table.add_column("Environment")
        table.add_column("Replicas")
        for component in deployment.components:
            replicas = str(component.replicas) if component.replicas is not None else "—"
            table.add_row(component.name, component.component_type, component.environment, replicas)
        return Panel(
            table,
            title=f"Deployment · {deployment.provider} · {', '.join(deployment.regions)} · {deployment.delivery_strategy}",
            border_style="blue",
        )

    @staticmethod
    def _tradeoffs_table(specification: ArchitectureSpecification) -> Table:
        """Render documented architectural decisions and accepted compromises."""
        table = Table(title="Trade-offs", box=ROUNDED, header_style="bold green")
        table.add_column("Decision", style="cyan")
        table.add_column("Benefits")
        table.add_column("Costs")
        table.add_column("Alternatives")
        for tradeoff in specification.tradeoffs:
            table.add_row(
                tradeoff.decision,
                "\n".join(tradeoff.benefits),
                "\n".join(tradeoff.costs),
                "\n".join(tradeoff.alternatives_considered) or "—",
            )
        if not specification.tradeoffs:
            table.add_row("—", "No trade-offs documented", "—", "—")
        return table
