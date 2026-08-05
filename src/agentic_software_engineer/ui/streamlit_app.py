"""Streamlit operator interface for the Agentic SDLC workflow."""

from __future__ import annotations

import asyncio
import io
import json
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

import streamlit as st
from openai import AsyncOpenAI

from agentic_software_engineer.domain.entities.architecture_specification import ArchitectureSpecification
from agentic_software_engineer.domain.entities.code_generation_plan import GeneratedArtifact, GenerationReport
from agentic_software_engineer.infrastructure.di.workflow_factory import build_workflow
from agentic_software_engineer.infrastructure.persistence.sqlite_store import (
    SQLiteExecutionStore,
    SQLiteProjectBuilder,
)
from agentic_software_engineer.orchestrator.state import AgenticSDLCState, WorkflowExecutionStatus, WorkflowTimestamps


def render_app() -> None:
    """Render the application and dispatch submitted workflow executions."""
    st.set_page_config(page_title="Agentic Software Engineer", page_icon="🧭", layout="wide")
    st.title("Agentic Software Engineer")
    st.caption("Requirements → planning → architecture → validated code generation")

    model = os.getenv("OPENAI_MODEL", "").strip()
    with st.sidebar:
        st.subheader("Runtime")
        st.text_input("OpenAI model", value=model or "Not configured", disabled=True)
        st.caption("Set `OPENAI_MODEL` and `OPENAI_API_KEY` in the process environment.")
        st.caption(f"SQLite: `{_database_store().database_path}`")
        if "execution_state" in st.session_state and st.button("Clear current result", use_container_width=True):
            del st.session_state["execution_state"]
            st.rerun()
        _render_saved_executions()

    with st.form("software_requirement"):
        project_name = st.text_input("Project name", value="generated-application", max_chars=100)
        requirement = st.text_area(
            "Software requirement",
            height=180,
            placeholder="Describe the application, users, APIs, data, constraints, and acceptance criteria...",
        )
        submitted = st.form_submit_button("Generate application", type="primary", use_container_width=True)

    if submitted:
        _handle_submission(project_name, requirement, model)

    state = st.session_state.get("execution_state")
    if isinstance(state, AgenticSDLCState):
        _render_state(state)


def _handle_submission(project_name: str, requirement: str, model: str) -> None:
    """Validate input, execute the workflow, and retain the typed final state."""
    if not project_name.strip():
        st.error("Project name is required.")
        return
    if not requirement.strip():
        st.error("Software requirement is required.")
        return
    if not model:
        st.error("OPENAI_MODEL is not configured.")
        return
    if not os.getenv("OPENAI_API_KEY", "").strip():
        st.error("OPENAI_API_KEY is not configured.")
        return

    status = st.status("Running Agentic SDLC workflow…", expanded=True)
    status.write("Analyzing requirements and planning engineering work.")
    try:
        state = asyncio.run(_execute_workflow(project_name.strip(), requirement.strip(), model))
    except Exception as error:
        status.update(label="Workflow execution failed", state="error", expanded=True)
        st.error(f"The workflow could not complete ({type(error).__name__}). Check application logs for details.")
        return

    st.session_state["execution_state"] = state
    if state.execution_status is WorkflowExecutionStatus.SUCCEEDED:
        status.update(label="Application generation completed", state="complete", expanded=False)
    elif state.execution_status is WorkflowExecutionStatus.AWAITING_APPROVAL:
        status.update(label="Workflow is waiting for approval", state="running", expanded=True)
    else:
        status.update(label="Workflow stopped with failures", state="error", expanded=True)


async def _execute_workflow(project_name: str, requirement: str, model: str) -> AgenticSDLCState:
    """Run a SQLite-backed workflow without creating generated source files."""
    execution_id = str(uuid4())
    logical_project_root = f"sqlite://generated-projects/{execution_id}"
    now = datetime.now(UTC)
    initial_state = AgenticSDLCState(
        execution_id=execution_id,
        project_name=project_name,
        project_root=logical_project_root,
        user_requirement=requirement,
        timestamps=WorkflowTimestamps(created_at=now, updated_at=now),
    )
    store = _database_store()
    store.save_execution_state(initial_state)
    client = AsyncOpenAI()
    workflow = build_workflow(
        client=client,
        model=model,
        project_builder=SQLiteProjectBuilder(store, execution_id),
        state_store=store,
    )
    final_state = await workflow.ainvoke(initial_state, thread_id=execution_id)
    return store.save_execution_state(final_state)


def _render_state(state: AgenticSDLCState) -> None:
    """Render durable workflow state as operator-focused views."""
    st.divider()
    status_column, execution_column, output_column, action_column = st.columns([1, 1.4, 2.6, 1])
    status_column.metric("Status", state.execution_status.value.replace("_", " ").title())
    execution_column.metric("Execution ID", state.execution_id[:12])
    output_column.text_input("Artifact storage", value=state.project_root or "", disabled=True)
    with action_column:
        st.write("")
        if st.button("Clear result", key="clear_displayed_result", use_container_width=True):
            st.session_state.pop("execution_state", None)
            st.rerun()

    overview_tab, requirements_tab, architecture_tab, code_tab, audit_tab = st.tabs(
        ["Overview", "Requirements & Plan", "Architecture", "Generated Code", "Audit"]
    )
    with overview_tab:
        _render_overview(state)
    with requirements_tab:
        _render_requirements_and_plan(state)
    with architecture_tab:
        _render_architecture(state)
    with code_tab:
        _render_generated_code(state)
    with audit_tab:
        _render_audit(state)


def _render_overview(state: AgenticSDLCState) -> None:
    """Render execution and generation metrics."""
    metrics = state.metrics
    columns = st.columns(5)
    columns[0].metric("Elapsed", f"{metrics.elapsed_time_ms / 1000:.1f}s")
    columns[1].metric("Input tokens", f"{metrics.total_input_tokens:,}")
    columns[2].metric("Output tokens", f"{metrics.total_output_tokens:,}")
    columns[3].metric("Retries", state.retry_count)
    columns[4].metric("Artifacts stored", len(_database_store().list_generated_artifacts(state.execution_id)))

    if state.approval_required:
        st.warning("Human approval is required before this workflow can continue.")
        if state.pending_approval_files:
            st.write("Pending files:", ", ".join(state.pending_approval_files))

    report = _generation_report(state)
    if report is not None:
        st.subheader("Generation outcome")
        st.dataframe(
            [
                {"Outcome": "Generated", "Count": len(report.generated_files)},
                {"Outcome": "Skipped", "Count": len(report.skipped_files)},
                {"Outcome": "Failed", "Count": len(report.failed_files)},
                {"Outcome": "Blocked", "Count": len(report.blocked_files)},
                {"Outcome": "Validation findings", "Count": len(report.validation_failures)},
            ],
            hide_index=True,
            use_container_width=True,
        )
        if report.validation_failures:
            with st.expander("Validation findings"):
                for finding in report.validation_failures:
                    st.write(f"- {finding}")


def _render_requirements_and_plan(state: AgenticSDLCState) -> None:
    """Render clarified requirements, assumptions, criteria, tasks, and dependencies."""
    left, right = st.columns(2)
    with left:
        st.subheader("Clarified requirements")
        _render_string_list(state.clarified_requirements, "No clarified requirements were produced.")
        st.subheader("Acceptance criteria")
        _render_string_list(state.acceptance_criteria, "No acceptance criteria were produced.")
    with right:
        st.subheader("Assumptions")
        _render_string_list(state.assumptions, "No assumptions were recorded.")

    st.subheader("Engineering tasks")
    st.dataframe(
        [
            {
                "ID": task.task_id,
                "Task": task.title,
                "Complexity": task.complexity,
                "Priority": task.priority,
                "Parallel": task.parallelizable,
                "Status": task.status.value,
            }
            for task in state.tasks
        ],
        hide_index=True,
        use_container_width=True,
    )
    with st.expander("Dependency graph"):
        st.dataframe(
            [
                {
                    "Predecessor": dependency.predecessor_task_id,
                    "Successor": dependency.successor_task_id,
                    "Type": dependency.dependency_type,
                    "Required": dependency.required,
                }
                for dependency in state.dependencies
            ],
            hide_index=True,
            use_container_width=True,
        )


def _render_architecture(state: AgenticSDLCState) -> None:
    """Render the approved architecture specification."""
    if not state.architecture:
        st.info("No architecture specification is available.")
        return
    architecture = ArchitectureSpecification.model_validate(state.architecture)
    st.subheader(architecture.project_name)
    st.write(architecture.business_goal)
    left, right = st.columns(2)
    left.metric("Architecture style", architecture.architecture_style)
    right.metric("Modules", len(architecture.modules))
    st.subheader("Technology stack")
    st.dataframe(
        [component.model_dump(mode="json") for component in architecture.technology_stack],
        hide_index=True,
        use_container_width=True,
    )
    st.subheader("Modules")
    st.dataframe(
        [
            {
                "Name": module.name,
                "Responsibility": module.responsibility,
                "Interfaces": ", ".join(module.interfaces),
                "Data owned": ", ".join(module.data_owned),
            }
            for module in architecture.modules
        ],
        hide_index=True,
        use_container_width=True,
    )
    with st.expander("Complete architecture JSON"):
        st.json(architecture.model_dump(mode="json"))


def _render_generated_code(state: AgenticSDLCState) -> None:
    """Render and download generated artifacts loaded directly from SQLite."""
    artifacts = _database_store().list_generated_artifacts(state.execution_id)
    if not artifacts:
        st.info("No validated code artifacts were generated.")
        return

    st.download_button(
        "Download generated project (.zip)",
        data=_artifact_archive(artifacts),
        file_name=f"{_safe_filename(state.project_name)}-{state.execution_id[:8]}.zip",
        mime="application/zip",
        use_container_width=True,
    )
    selected_path = st.selectbox("File", [artifact.path for artifact in artifacts])
    selected = next(artifact for artifact in artifacts if artifact.path == selected_path)
    details = st.columns(3)
    details[0].metric("Status", selected.validation_status.value)
    details[1].metric("Attempt", selected.attempt_number)
    details[2].metric("Model", selected.model)
    st.code(selected.content, language=_source_language(selected.path), line_numbers=True)
    st.download_button(
        "Download selected file",
        data=selected.content,
        file_name=PurePosixPath(selected.path).name,
        mime="text/plain",
    )


def _render_audit(state: AgenticSDLCState) -> None:
    """Render safe workflow transition history without prompt or source content."""
    st.dataframe(
        [
            {
                "Time": entry.timestamp.isoformat(),
                "Agent": entry.agent_name or "workflow",
                "Stage": entry.stage.value if entry.stage else "",
                "Status": entry.status.value,
                "Event": entry.event_type,
                "Summary": entry.summary,
            }
            for entry in state.execution_history
        ],
        hide_index=True,
        use_container_width=True,
    )


def _generation_report(state: AgenticSDLCState) -> GenerationReport | None:
    """Hydrate a strict report from either typed or checkpoint-JSON state."""
    report = state.generation_report
    if report is None:
        return None
    if isinstance(report, GenerationReport):
        return report
    return GenerationReport.model_validate_json(json.dumps(report))


def _artifact_archive(artifacts: list[GeneratedArtifact]) -> bytes:
    """Return a ZIP archive assembled in memory from SQLite artifacts."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for artifact in artifacts:
            archive.writestr(artifact.path.replace("\\", "/"), artifact.content)
    return buffer.getvalue()


@st.cache_resource
def _database_store() -> SQLiteExecutionStore:
    """Return the process-shared Streamlit SQLite repository."""
    return SQLiteExecutionStore(Path.cwd() / "data" / "agentic_sdlc.sqlite3")


def _render_saved_executions() -> None:
    """Allow an operator to restore a durable prior execution from SQLite."""
    executions = _database_store().list_executions()
    if not executions:
        return
    st.divider()
    st.subheader("Saved executions")
    identifiers = [execution.execution_id for execution in executions]
    summaries = {execution.execution_id: execution for execution in executions}
    selected_id = st.selectbox(
        "Execution",
        identifiers,
        format_func=lambda execution_id: (
            f"{summaries[execution_id].project_name} · "
            f"{summaries[execution_id].status} · {execution_id[:8]}"
        ),
    )
    if st.button("Load execution", use_container_width=True):
        loaded = _database_store().load_execution_state(selected_id)
        if loaded is not None:
            st.session_state["execution_state"] = loaded
            st.rerun()


def _render_string_list(values: list[str], empty_message: str) -> None:
    """Render a concise list or its empty-state message."""
    if not values:
        st.caption(empty_message)
        return
    for value in values:
        st.write(f"- {value}")


def _source_language(path: str) -> str:
    """Map a generated path to a Streamlit code-highlighting language."""
    suffix = PurePosixPath(path).suffix.casefold()
    return {".py": "python", ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".md": "markdown"}.get(
        suffix, "text"
    )


def _safe_filename(value: str) -> str:
    """Return a safe filename component for a browser download."""
    normalized = "".join(character if character.isalnum() or character in "-_" else "-" for character in value)
    return normalized.strip("-") or "generated-project"
