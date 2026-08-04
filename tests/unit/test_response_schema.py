from agentic_software_engineer.agents.base import build_openai_json_schema
from agentic_software_engineer.agents.planning_agent import PlanningOutput
from agentic_software_engineer.agents.requirement_agent import RequirementAnalysis
from agentic_software_engineer.domain.entities.architecture_specification import ArchitectureSpecification
from typing import Any


def _find_object_schema_with_property(schema: dict[str, Any], property_name: str) -> dict[str, Any] | None:
    if schema.get("type") == "object" and isinstance(schema.get("properties"), dict) and property_name in schema["properties"]:
        return schema
    for value in schema.values():
        if isinstance(value, dict):
            result = _find_object_schema_with_property(value, property_name)
            if result is not None:
                return result
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    result = _find_object_schema_with_property(item, property_name)
                    if result is not None:
                        return result
    return None


def test_openai_schema_forbids_additional_properties() -> None:
    requirement_schema = build_openai_json_schema(RequirementAnalysis)
    planning_schema = build_openai_json_schema(PlanningOutput)

    assert requirement_schema.get("additionalProperties") is False
    assert planning_schema.get("additionalProperties") is False


def test_openai_architecture_schema_requires_optional_fields() -> None:
    architecture_schema = build_openai_json_schema(ArchitectureSpecification)
    api_definition_schema = _find_object_schema_with_property(architecture_schema, "authorization_policy")

    assert api_definition_schema is not None
    assert api_definition_schema.get("additionalProperties") is False
    assert "required" in api_definition_schema
    assert "authorization_policy" in api_definition_schema["required"]
