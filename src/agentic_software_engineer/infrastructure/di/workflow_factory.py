"""Dependency-injection composition for executable Agentic SDLC workflows."""

from __future__ import annotations

from pathlib import Path

from openai import AsyncOpenAI

from agentic_software_engineer.agents.architecture_agent import ArchitectureAgent
from agentic_software_engineer.agents.coding_agent import CodingAgent
from agentic_software_engineer.agents.planning_agent import PlanningAgent
from agentic_software_engineer.agents.prompt_loader import FilePromptLoader
from agentic_software_engineer.agents.requirement_agent import RequirementAgent
from agentic_software_engineer.codegen.architecture_code_plan_generator import ArchitectureCodePlanGenerator
from agentic_software_engineer.codegen.dependency_resolver import DependencyResolver
from agentic_software_engineer.codegen.generation_executor import GenerationExecutor
from agentic_software_engineer.codegen.generic_generator import GenericCodeGenerator
from agentic_software_engineer.codegen.project_builder import ProjectBuilder
from agentic_software_engineer.llm.openai_client import OpenAIClientConfiguration, OpenAILLMClient
from agentic_software_engineer.memory.in_memory_state_store import InMemorySharedStateStore
from agentic_software_engineer.orchestrator.workflow import AgenticSDLCWorkflow
from agentic_software_engineer.prompts.file_prompt_registry import FilePromptRegistry
from agentic_software_engineer.validators.code_validator import CodeValidator


def build_workflow(*, client: AsyncOpenAI, model: str, project_root: Path) -> AgenticSDLCWorkflow:
    """Compose a fully configured workflow for one isolated project workspace."""
    prompt_root = Path(__file__).resolve().parents[2] / "prompts" / "coding"
    generator = GenericCodeGenerator(
        llm_client=OpenAILLMClient(client, OpenAIClientConfiguration(model=model)),
        prompt_registry=FilePromptRegistry(prompt_root),
        prompt_loader=FilePromptLoader(),
    )
    project_builder = ProjectBuilder(project_root)
    executor = GenerationExecutor(
        generic_generator=generator,
        code_validator=CodeValidator(),
        project_builder=project_builder,
        dependency_resolver=DependencyResolver(),
    )
    coding_agent = CodingAgent(
        code_plan_generator=ArchitectureCodePlanGenerator(),
        generation_executor=executor,
        state_store=InMemorySharedStateStore.get_instance(),
    )
    return AgenticSDLCWorkflow(
        requirement_agent=RequirementAgent(client=client, model=model),
        planning_agent=PlanningAgent(client=client, model=model),
        architecture_agent=ArchitectureAgent(client=client, model=model),
        coding_agent=coding_agent,
    )
