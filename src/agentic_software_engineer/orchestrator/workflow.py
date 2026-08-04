"""LangGraph workflow composition for the Agentic SDLC platform."""

from __future__ import annotations

import logging
from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agentic_software_engineer.agents.architecture_agent import ArchitectureAgent
from agentic_software_engineer.agents.base import BaseAgent
from agentic_software_engineer.agents.coding_agent import CodingAgent
from agentic_software_engineer.agents.planning_agent import PlanningAgent
from agentic_software_engineer.agents.requirement_agent import RequirementAgent
from agentic_software_engineer.orchestrator.state import AgenticSDLCState as AgentState
from agentic_software_engineer.orchestrator.state import WorkflowExecutionStatus, WorkflowStage
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file for local development

class AgenticSDLCWorkflow:
    """Build the checkpointed, approval-aware Agentic SDLC LangGraph workflow.

    The graph deliberately keeps recovery paths bounded.  ``retry_count`` is
    durable state, so a resumed execution cannot create a fresh, unbounded
    repair loop.  Production callers should inject a durable checkpointer; the
    ``MemorySaver`` default exists solely for local development.
    """

    def __init__(
        self,
        requirement_agent: RequirementAgent,
        planning_agent: PlanningAgent,
        architecture_agent: ArchitectureAgent,
        coding_agent: CodingAgent | None = None,
        *,
        checkpointer: BaseCheckpointSaver | None = None,
        max_workflow_retries: int = 2,
        logger: logging.Logger | None = None,
    ) -> None:
        """Create a workflow with injected agents and checkpoint persistence.

        Args:
            requirement_agent: Agent that structures the original requirement.
            planning_agent: Agent that generates tasks and dependencies.
            architecture_agent: Agent that produces the architecture artifact.
            coding_agent: Agent that generates and validates approved files.
                When omitted, the legacy requirement-to-architecture flow is
                retained for composition roots that have not yet configured the
                code-generation dependencies.
            checkpointer: Durable LangGraph checkpoint adapter.
            max_workflow_retries: Maximum repair-node executions per workflow.
            logger: Application logger supplied by dependency injection.

        Raises:
            ValueError: If ``max_workflow_retries`` is negative.
        """
        if max_workflow_retries < 0:
            raise ValueError("max_workflow_retries must be non-negative.")

        self._requirement_agent = requirement_agent
        self._planning_agent = planning_agent
        self._architecture_agent = architecture_agent
        self._coding_agent = coding_agent
        self._checkpointer = checkpointer or MemorySaver()
        self._max_workflow_retries = max_workflow_retries
        self._logger = logger or logging.getLogger(__name__)

    def build(self) -> Any:
        """Compile the checkpointed graph with bounded repair branches.

        The primary path is ``START -> requirement -> planning -> architecture
        -> coding -> END``.  Every node is checkpointed by the configured
        checkpointer. Call :meth:`ainvoke` to enforce that ``execution_id`` is
        supplied as the LangGraph ``thread_id``.
        """
        graph = StateGraph(AgentState)
        self._register_agent_node(graph, "requirement_agent", self._requirement_agent, WorkflowStage.REQUIREMENTS)
        self._register_agent_node(graph, "planning_agent", self._planning_agent, WorkflowStage.PLANNING)
        self._register_agent_node(graph, "architecture_agent", self._architecture_agent, WorkflowStage.ARCHITECTURE)
        if self._coding_agent is not None:
            self._register_agent_node(graph, "coding_node", self._coding_agent, WorkflowStage.CODING)
        self._register_repair_node(graph, "architecture_repair", self._architecture_agent, WorkflowStage.ARCHITECTURE)
        if self._coding_agent is not None:
            self._register_repair_node(graph, "coding_repair", self._coding_agent, WorkflowStage.CODING)
        self._register_safe_stop_node(graph)

        graph.add_edge(START, "requirement_agent")
        self._add_guarded_transition(graph, "requirement_agent", "planning_agent")
        self._add_guarded_transition(graph, "planning_agent", "architecture_agent")
        architecture_routes = {
            "architecture_repair": "architecture_repair",
            "end": END,
            "safe_stop": "safe_stop",
        }
        if self._coding_agent is not None:
            architecture_routes["coding"] = "coding_node"
        else:
            # Compatibility for the existing console composition while the
            # code-plan/generation dependencies are introduced separately.
            architecture_routes["coding"] = END
            self._logger.warning("Coding agent is not configured; workflow will end after architecture.")
        graph.add_conditional_edges("architecture_agent", self.route_after_architecture, architecture_routes)
        graph.add_conditional_edges("architecture_repair", self.route_after_architecture, architecture_routes)
        if self._coding_agent is not None:
            graph.add_conditional_edges(
                "coding_node",
                self.route_after_coding,
                {"coding_repair": "coding_repair", "end": END, "safe_stop": "safe_stop"},
            )
            graph.add_conditional_edges(
                "coding_repair",
                self.route_after_coding,
                {"coding_repair": "coding_repair", "end": END, "safe_stop": "safe_stop"},
            )
        graph.add_edge("safe_stop", END)

        self._logger.info("Compiling Agentic SDLC workflow with checkpoint support")
        return graph.compile(checkpointer=self._checkpointer)

    async def ainvoke(self, state: AgentState, *, thread_id: str | None = None) -> AgentState:
        """Run the graph using the execution ID as the required checkpoint key.

        Args:
            state: Durable workflow state to execute or resume.
            thread_id: LangGraph checkpoint key. It must exactly equal
                ``state.execution_id``.

        Raises:
            ValueError: If a thread ID is omitted or does not match the state.
        """
        if not thread_id:
            raise ValueError("LangGraph thread_id is required for every execution.")
        if thread_id != state.execution_id:
            raise ValueError("LangGraph thread_id must match AgentState.execution_id.")

        result = await self.build().ainvoke(
            state,
            config={"configurable": {"thread_id": thread_id}},
        )
        return AgentState.model_validate(result)

    def _register_agent_node(
        self,
        graph: StateGraph,
        node_name: str,
        agent: BaseAgent,
        stage: WorkflowStage,
    ) -> None:
        """Register one ordinary state-preserving agent node."""

        async def execute_node(state: AgentState) -> AgentState:
            staged_state = state.model_copy(update={"current_stage": stage}, deep=True)
            self._logger.info(
                "Executing workflow node",
                extra={"execution_id": state.execution_id, "node_name": node_name, "agent_name": agent.name},
            )
            return await agent.run(staged_state)

        graph.add_node(node_name, execute_node)

    def _register_repair_node(
        self,
        graph: StateGraph,
        node_name: str,
        agent: BaseAgent,
        stage: WorkflowStage,
    ) -> None:
        """Register a bounded, explicit retry node for one failed stage."""

        async def repair_node(state: AgentState) -> AgentState:
            retry_state = state.model_copy(
                update={
                    "current_stage": stage,
                    "current_agent": agent.name,
                    "execution_status": WorkflowExecutionStatus.RETRYING,
                    "retry_count": state.retry_count + 1,
                },
                deep=True,
            )
            self._logger.info(
                "Executing workflow repair node",
                extra={"execution_id": state.execution_id, "node_name": node_name, "retry_count": retry_state.retry_count},
            )
            try:
                result = await agent.retry(retry_state)
                result = await agent.validate(result)
                result = await agent.report(result)
                status = (
                    WorkflowExecutionStatus.AWAITING_APPROVAL
                    if result.approval_required
                    else WorkflowExecutionStatus.SUCCEEDED
                )
                return result.model_copy(update={"execution_status": status}, deep=True)
            except Exception as error:  # Agent errors must remain routable state.
                self._logger.warning(
                    "Workflow repair node failed",
                    extra={"execution_id": state.execution_id, "node_name": node_name, "error_type": type(error).__name__},
                )
                return retry_state.model_copy(update={"execution_status": WorkflowExecutionStatus.FAILED}, deep=True)

        graph.add_node(node_name, repair_node)

    def _register_safe_stop_node(self, graph: StateGraph) -> None:
        """Register the terminal node used after bounded recovery is exhausted."""

        def safe_stop(state: AgentState) -> AgentState:
            self._logger.error(
                "Workflow entered safe stop",
                extra={"execution_id": state.execution_id, "retry_count": state.retry_count},
            )
            return state.model_copy(update={"execution_status": WorkflowExecutionStatus.FAILED}, deep=True)

        graph.add_node("safe_stop", safe_stop)

    def _add_guarded_transition(self, graph: StateGraph, source_node: str, success_node: str) -> None:
        """Advance requirements and planning only after successful completion."""
        graph.add_conditional_edges(
            source_node,
            self._route_after_agent,
            {"continue": success_node, "end": END},
        )

    @staticmethod
    def _route_after_agent(state: AgentState) -> Literal["continue", "end"]:
        """Route successful prerequisite stages forward; preserve all other outcomes."""
        return "continue" if state.execution_status is WorkflowExecutionStatus.SUCCEEDED else "end"

    def route_after_architecture(
        self,
        state: AgentState,
    ) -> Literal["coding", "architecture_repair", "end", "safe_stop"]:
        """Route architecture outcomes to approval, coding, or bounded repair."""
        if state.approval_required:
            return "end"
        if state.execution_status is WorkflowExecutionStatus.FAILED:
            return "architecture_repair" if self._can_retry(state) else "safe_stop"
        return "coding" if state.execution_status is WorkflowExecutionStatus.SUCCEEDED else "end"

    def route_after_coding(self, state: AgentState) -> Literal["coding_repair", "end", "safe_stop"]:
        """Route coding outcomes without allowing an unbounded generation loop."""
        if state.approval_required:
            return "end"
        if self._has_blocking_generation_failures(state):
            return "coding_repair" if self._can_retry(state) else "safe_stop"
        return "end"

    def _can_retry(self, state: AgentState) -> bool:
        """Return whether durable retry budget permits one more repair node."""
        return state.retry_count < self._max_workflow_retries

    @staticmethod
    def _has_blocking_generation_failures(state: AgentState) -> bool:
        """Return whether a report contains blocked or required-file failures.

        The state can be restored from a checkpoint as either validated models
        or JSON-compatible dictionaries, so this deliberately avoids assuming
        one representation. A report without its corresponding plan is handled
        conservatively: its failures are treated as blocking.
        """
        report = state.generation_report
        if report is None:
            return state.execution_status is WorkflowExecutionStatus.FAILED
        if isinstance(report, dict):
            failed_file_ids = set(report.get("failed_files", []))
            blocked_file_ids = report.get("blocked_files", [])
        else:
            failed_file_ids = set(report.failed_files)
            blocked_file_ids = report.blocked_files

        if blocked_file_ids:
            return True
        if not failed_file_ids:
            return False

        plan = state.code_generation_plan
        if plan is None:
            return True
        files = plan.get("files", []) if isinstance(plan, dict) else plan.files
        required_file_ids = {
            file_specification.get("id")
            if isinstance(file_specification, dict)
            else file_specification.id
            for file_specification in files
            if (
                file_specification.get("required", True)
                if isinstance(file_specification, dict)
                else file_specification.required
            )
        }
        return bool(failed_file_ids.intersection(required_file_ids))
