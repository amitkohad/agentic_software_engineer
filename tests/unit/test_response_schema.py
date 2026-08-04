from agentic_software_engineer.agents.base import build_openai_json_schema
from agentic_software_engineer.agents.planning_agent import PlanningOutput
from agentic_software_engineer.agents.requirement_agent import RequirementAnalysis


def test_openai_schema_forbids_additional_properties() -> None:
    requirement_schema = build_openai_json_schema(RequirementAnalysis)
    planning_schema = build_openai_json_schema(PlanningOutput)

    assert requirement_schema.get("additionalProperties") is False
    assert planning_schema.get("additionalProperties") is False
