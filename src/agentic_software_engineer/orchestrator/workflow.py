"""LangGraph workflow composition for the Agentic SDLC platform."""

from __future__ import annotations

import logging
from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agentic_software_engineer.agents.base import BaseAgent
from agentic_software_engineer.agents.architecture_agent import ArchitectureAgent
from agentic_software_engineer.agents.planning_agent import PlanningAgent
from agentic_software_engineer.agents.requirement_agent import RequirementAgent
from agentic_software_engineer.orchestrator.state import AgenticSDLCState as AgentState
from agentic_software_engineer.orchestrator.state import WorkflowExecutionStatus, WorkflowStage
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
        architecture_agent: ArchitectureAgent,
        *,
        checkpointer: BaseCheckpointSaver | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Create a workflow with injected agents and checkpoint persistence.

        Args:
            requirement_agent: Agent that structures the original requirement.
            planning_agent: Agent that generates tasks and dependencies.
            architecture_agent: Agent that generates the validated architecture artifact.
            checkpointer: LangGraph checkpoint adapter. A process-local
                ``MemorySaver`` is used when omitted.
            logger: Application logger supplied by dependency injection.
        """
        self._requirement_agent = requirement_agent
        self._planning_agent = planning_agent
        self._architecture_agent = architecture_agent
        self._checkpointer = checkpointer or MemorySaver()
        self._logger = logger or logging.getLogger(__name__)

    def build(self) -> Any:
        """Compile the current checkpointed workflow graph.

        Returns:
            A LangGraph compiled graph with the flow ``START -> requirement ->
            planning -> architecture -> END``. The checkpointer persists state
            after each graph node. Callers must pass a stable ``thread_id`` in
            their LangGraph invocation configuration to resume from checkpoints.
        """
        graph = StateGraph(AgentState)
        self._register_agent_node(graph, "requirement_agent", self._requirement_agent, WorkflowStage.REQUIREMENTS)
        self._register_agent_node(graph, "planning_agent", self._planning_agent, WorkflowStage.PLANNING)
        self._register_agent_node(graph, "architecture_agent", self._architecture_agent, WorkflowStage.ARCHITECTURE)

        graph.add_edge(START, "requirement_agent")
        self._add_guarded_transition(graph, "requirement_agent", "planning_agent")
        self._add_guarded_transition(graph, "planning_agent", "architecture_agent")
        self._add_guarded_transition(graph, "architecture_agent", None)

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

    def _add_guarded_transition(
        self,
        graph: StateGraph,
        source_node: str,
        success_node: str | None,
    ) -> None:
        """Add a branch-ready edge that advances only after a successful stage.

        Failed, cancelled, or approval-paused states terminate the current graph
        invocation while the checkpointer retains the durable state. Future
        branching policies can extend this method with additional route labels
        without changing agent-node implementations.
        """
        routes: dict[Literal["continue", "end"], str] = {
            "continue": success_node or END,
            "end": END,
        }
        graph.add_conditional_edges(
            source_node,
            self._route_after_agent,
            routes,
        )

    @staticmethod
    def _route_after_agent(state: AgentState) -> Literal["continue", "end"]:
        """Route successful states forward and preserve all other outcomes."""
        if state.execution_status is WorkflowExecutionStatus.SUCCEEDED:
            return "continue"
        return "end"
