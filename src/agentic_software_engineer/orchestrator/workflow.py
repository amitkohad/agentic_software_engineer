"""LangGraph workflow composition for the Agentic SDLC platform."""

from __future__ import annotations

import logging
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agentic_software_engineer.agents.base import BaseAgent
from agentic_software_engineer.agents.planning_agent import PlanningAgent
from agentic_software_engineer.agents.requirement_agent import RequirementAgent
from agentic_software_engineer.orchestrator.state import AgenticSDLCState as AgentState
from agentic_software_engineer.orchestrator.state import WorkflowStage
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

class AgenticSDLCWorkflow:
    """Build the checkpointed LangGraph workflow for enterprise SDLC execution.

    Agents and the checkpoint saver are injected at the composition boundary.
    A ``MemorySaver`` is available only as a safe local-development default;
    production composition should provide a durable, tenant-aware checkpoint
    implementation. Additional agent stages can be registered by extending the
    builder before compilation without changing existing node contracts.
    """

    def __init__(
        self,
        requirement_agent: RequirementAgent,
        planning_agent: PlanningAgent,
        *,
        checkpointer: BaseCheckpointSaver | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Create a workflow with injected agents and checkpoint persistence.

        Args:
            requirement_agent: Agent that structures the original requirement.
            planning_agent: Agent that generates tasks and dependencies.
            checkpointer: LangGraph checkpoint adapter. A process-local
                ``MemorySaver`` is used when omitted.
            logger: Application logger supplied by dependency injection.
        """
        self._requirement_agent = requirement_agent
        self._planning_agent = planning_agent
        self._checkpointer = checkpointer or MemorySaver()
        self._logger = logger or logging.getLogger(__name__)

    def build(self) -> Any:
        """Compile the current checkpointed workflow graph.

        Returns:
            A LangGraph compiled graph with the flow ``START -> requirement ->
            planning -> END``. Callers must pass a stable ``thread_id`` in their
            LangGraph invocation configuration to resume from checkpoints.
        """
        graph = StateGraph(AgentState)
        self._register_agent_node(graph, "requirement_agent", self._requirement_agent, WorkflowStage.REQUIREMENTS)
        self._register_agent_node(graph, "planning_agent", self._planning_agent, WorkflowStage.PLANNING)

        graph.add_edge(START, "requirement_agent")
        graph.add_edge("requirement_agent", "planning_agent")
        graph.add_edge("planning_agent", END)

        self._logger.info("Compiling Agentic SDLC workflow with checkpoint support")
        return graph.compile(checkpointer=self._checkpointer)

    def _register_agent_node(
        self,
        graph: StateGraph,
        node_name: str,
        agent: BaseAgent,
        stage: WorkflowStage,
    ) -> None:
        """Register one state-preserving agent node for future graph expansion."""

        async def execute_node(state: AgentState) -> AgentState:
            staged_state = state.model_copy(update={"current_stage": stage}, deep=True)
            self._logger.info(
                "Executing workflow node",
                extra={
                    "execution_id": state.execution_id,
                    "node_name": node_name,
                    "agent_name": agent.name,
                    "stage": stage.value,
                },
            )
            result = await agent.run(staged_state)
            self._logger.info(
                "Workflow node completed",
                extra={
                    "execution_id": result.execution_id,
                    "node_name": node_name,
                    "status": result.execution_status.value,
                },
            )
            return result

        graph.add_node(node_name, execute_node)
