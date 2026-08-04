"""Strict Pydantic contracts for enterprise software architecture artifacts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ArchitectureModel(BaseModel):
    """Base model that rejects undeclared fields and type coercion."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class TechnologyComponent(ArchitectureModel):
    """A technology selected for a clearly bounded platform responsibility."""

    name: str = Field(min_length=1, description="Technology or product name.")
    category: str = Field(min_length=1, description="Responsibility category, such as runtime or database.")
    version: str | None = Field(default=None, description="Approved version or version range.")
    purpose: str = Field(min_length=1, description="Reason this technology is included.")


class ModuleDependency(ArchitectureModel):
    """A directed dependency from one module to another."""

    target_module: str = Field(min_length=1, description="Identifier of the depended-on module.")
    dependency_type: str = Field(min_length=1, description="Nature of the dependency.")
    rationale: str = Field(min_length=1, description="Reason the dependency is necessary.")


class ModuleSpecification(ArchitectureModel):
    """A deployable, bounded-context, or code-level module definition."""

    name: str = Field(min_length=1, description="Stable module identifier.")
    responsibility: str = Field(min_length=1, description="Primary responsibility owned by the module.")
    interfaces: list[str] = Field(default_factory=list, description="Public interfaces exposed by the module.")
    dependencies: list[ModuleDependency] = Field(default_factory=list, description="Outbound module dependencies.")
    data_owned: list[str] = Field(default_factory=list, description="Data entities or stores owned by the module.")


class ApiParameter(ArchitectureModel):
    """A typed request parameter for an API operation."""

    name: str = Field(min_length=1, description="Parameter name.")
    location: str = Field(min_length=1, description="Parameter location, such as path, query, header, or body.")
    data_type: str = Field(min_length=1, description="Contract-level parameter data type.")
    required: bool = Field(description="Whether clients must supply the parameter.")
    description: str = Field(min_length=1, description="Parameter semantics.")


class ApiResponse(ArchitectureModel):
    """A documented response variant for an API operation."""

    status_code: int = Field(ge=100, le=599, description="HTTP response status code.")
    description: str = Field(min_length=1, description="Meaning of this response.")
    schema_reference: str | None = Field(default=None, description="Reference to the response schema.")


class ApiDefinition(ArchitectureModel):
    """A versioned external or internal HTTP API operation."""

    operation_id: str = Field(min_length=1, description="Stable API operation identifier.")
    method: str = Field(min_length=1, description="HTTP method.")
    path: str = Field(min_length=1, description="Versioned endpoint path.")
    summary: str = Field(min_length=1, description="Concise operation purpose.")
    authentication_required: bool = Field(description="Whether the operation requires authentication.")
    authorization_policy: str | None = Field(default=None, description="Authorization policy or scope requirement.")
    parameters: list[ApiParameter] = Field(default_factory=list, description="Operation request parameters.")
    responses: list[ApiResponse] = Field(default_factory=list, description="Documented operation responses.")


class DatabaseColumn(ArchitectureModel):
    """A relational or document-schema field definition."""

    name: str = Field(min_length=1, description="Column or field name.")
    data_type: str = Field(min_length=1, description="Storage data type.")
    nullable: bool = Field(description="Whether a null value is permitted.")
    primary_key: bool = Field(default=False, description="Whether this field participates in the primary key.")
    unique: bool = Field(default=False, description="Whether this field requires uniqueness.")
    description: str = Field(min_length=1, description="Business meaning of the stored value.")


class DatabaseIndex(ArchitectureModel):
    """An index or access-path requirement for a database table."""

    name: str = Field(min_length=1, description="Index name.")
    columns: list[str] = Field(min_length=1, description="Ordered indexed columns.")
    unique: bool = Field(default=False, description="Whether the index enforces uniqueness.")
    rationale: str = Field(min_length=1, description="Query or integrity need served by the index.")


class DatabaseTable(ArchitectureModel):
    """A persistent database table, collection, or aggregate store."""

    name: str = Field(min_length=1, description="Physical storage object name.")
    purpose: str = Field(min_length=1, description="Data responsibility of the object.")
    columns: list[DatabaseColumn] = Field(min_length=1, description="Stored field definitions.")
    indexes: list[DatabaseIndex] = Field(default_factory=list, description="Required indexes.")
    retention_policy: str | None = Field(default=None, description="Data retention and deletion policy.")


class DatabaseSchema(ArchitectureModel):
    """The persistent data design for the architecture."""

    database_type: str = Field(min_length=1, description="Database technology or data-store category.")
    schema_name: str | None = Field(default=None, description="Logical schema or namespace name.")
    tables: list[DatabaseTable] = Field(default_factory=list, description="Persistent data objects.")
    migration_strategy: str = Field(min_length=1, description="Schema evolution and migration approach.")
    backup_and_recovery: str = Field(min_length=1, description="Backup, restore, and recovery strategy.")


class DomainAttribute(ArchitectureModel):
    """A business attribute belonging to a domain entity."""

    name: str = Field(min_length=1, description="Domain attribute name.")
    data_type: str = Field(min_length=1, description="Domain-level data type.")
    required: bool = Field(description="Whether the attribute is mandatory.")
    description: str = Field(min_length=1, description="Business meaning and constraints.")


class DomainEntity(ArchitectureModel):
    """A business entity or aggregate represented by the system."""

    name: str = Field(min_length=1, description="Entity or aggregate name.")
    description: str = Field(min_length=1, description="Business responsibility and lifecycle.")
    identifier: str = Field(min_length=1, description="Primary identity attribute.")
    attributes: list[DomainAttribute] = Field(default_factory=list, description="Entity attributes.")
    invariants: list[str] = Field(default_factory=list, description="Rules that must always hold true.")
    relationships: list[str] = Field(default_factory=list, description="Relationships to other domain entities.")


class SequenceStep(ArchitectureModel):
    """One ordered interaction in a system sequence flow."""

    order: int = Field(ge=1, description="One-based sequence position.")
    source: str = Field(min_length=1, description="Initiating component or actor.")
    target: str = Field(min_length=1, description="Receiving component or actor.")
    action: str = Field(min_length=1, description="Interaction performed.")
    failure_handling: str | None = Field(default=None, description="Expected behavior on interaction failure.")


class SequenceFlow(ArchitectureModel):
    """A named end-to-end interaction flow between architectural components."""

    name: str = Field(min_length=1, description="Flow identifier.")
    trigger: str = Field(min_length=1, description="Event that begins the flow.")
    steps: list[SequenceStep] = Field(min_length=1, description="Ordered component interactions.")
    outcome: str = Field(min_length=1, description="Expected successful flow outcome.")


class DeploymentComponent(ArchitectureModel):
    """A workload, managed service, or infrastructure component in deployment."""

    name: str = Field(min_length=1, description="Deployment component name.")
    component_type: str = Field(min_length=1, description="Workload or infrastructure classification.")
    environment: str = Field(min_length=1, description="Target environment, such as production.")
    replicas: int | None = Field(default=None, ge=0, description="Target replica count when applicable.")
    dependencies: list[str] = Field(default_factory=list, description="Required deployment dependencies.")


class DeploymentArchitecture(ArchitectureModel):
    """Infrastructure topology and release controls for the system."""

    provider: str = Field(min_length=1, description="Cloud or infrastructure provider.")
    regions: list[str] = Field(min_length=1, description="Deployment regions or zones.")
    components: list[DeploymentComponent] = Field(min_length=1, description="Deployed components.")
    delivery_strategy: str = Field(min_length=1, description="Release strategy, such as blue-green or canary.")
    disaster_recovery_strategy: str = Field(min_length=1, description="Disaster recovery approach and target objectives.")


class SecurityControl(ArchitectureModel):
    """A security requirement mapped to a system control."""

    control_id: str = Field(min_length=1, description="Stable control identifier.")
    category: str = Field(min_length=1, description="Security domain, such as identity or encryption.")
    description: str = Field(min_length=1, description="Control behavior and scope.")
    implementation_owner: str = Field(min_length=1, description="Team or module accountable for the control.")
    verification_method: str = Field(min_length=1, description="Evidence or test used to verify the control.")


class ObservabilitySpecification(ArchitectureModel):
    """Logs, metrics, traces, and alerting expectations for operations."""

    logging_strategy: str = Field(min_length=1, description="Structured logging and retention approach.")
    metrics: list[str] = Field(default_factory=list, description="Required operational and business metrics.")
    tracing_strategy: str = Field(min_length=1, description="Distributed tracing approach.")
    alerting_rules: list[str] = Field(default_factory=list, description="Operational alert conditions.")
    dashboard_requirements: list[str] = Field(default_factory=list, description="Required operational dashboards.")


class ScalabilitySpecification(ArchitectureModel):
    """Performance, capacity, and elasticity requirements for the system."""

    expected_load: str = Field(min_length=1, description="Expected workload volume and concurrency.")
    performance_targets: list[str] = Field(default_factory=list, description="Latency and throughput objectives.")
    scaling_strategy: str = Field(min_length=1, description="Horizontal, vertical, or event-driven scaling approach.")
    capacity_limits: list[str] = Field(default_factory=list, description="Known resource or service limits.")


class Risk(ArchitectureModel):
    """A tracked technical, operational, security, or delivery risk."""

    description: str = Field(min_length=1, description="Risk statement.")
    likelihood: str = Field(min_length=1, description="Estimated likelihood rating.")
    impact: str = Field(min_length=1, description="Estimated impact rating.")
    mitigation: str = Field(min_length=1, description="Planned risk treatment.")
    owner: str | None = Field(default=None, description="Accountable role or team.")


class Tradeoff(ArchitectureModel):
    """A documented architectural decision and its accepted compromise."""

    decision: str = Field(min_length=1, description="Architecture choice made.")
    benefits: list[str] = Field(min_length=1, description="Benefits gained from the decision.")
    costs: list[str] = Field(min_length=1, description="Costs or limitations accepted.")
    alternatives_considered: list[str] = Field(default_factory=list, description="Alternatives evaluated.")


class ArchitectureSpecification(ArchitectureModel):
    """Complete, immutable enterprise architecture specification for a project."""

    project_name: str = Field(min_length=1, description="Name of the software project.")
    business_goal: str = Field(min_length=1, description="Business outcome the architecture must enable.")
    architecture_style: str = Field(min_length=1, description="Primary architecture style or pattern.")
    technology_stack: list[TechnologyComponent] = Field(min_length=1, description="Approved technology choices.")
    modules: list[ModuleSpecification] = Field(min_length=1, description="Logical and deployable system modules.")
    api_definitions: list[ApiDefinition] = Field(default_factory=list, description="Internal and external API contracts.")
    database_schema: DatabaseSchema | None = Field(default=None, description="Persistent data architecture when applicable.")
    domain_entities: list[DomainEntity] = Field(default_factory=list, description="Core domain entity definitions.")
    sequence_flows: list[SequenceFlow] = Field(default_factory=list, description="Key end-to-end system interactions.")
    deployment_architecture: DeploymentArchitecture | None = Field(default=None, description="Target deployment topology.")
    security_controls: list[SecurityControl] = Field(default_factory=list, description="Required security controls.")
    observability: ObservabilitySpecification | None = Field(default=None, description="Operational observability design.")
    scalability: ScalabilitySpecification | None = Field(default=None, description="Capacity and scaling design.")
    risks: list[Risk] = Field(default_factory=list, description="Tracked architecture risks.")
    tradeoffs: list[Tradeoff] = Field(default_factory=list, description="Documented decision tradeoffs.")
    assumptions: list[str] = Field(default_factory=list, description="Explicit architecture assumptions.")
    implementation_notes: list[str] = Field(default_factory=list, description="Implementation guidance and constraints.")
