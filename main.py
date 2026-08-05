"""Console entry point for the Agentic SDLC workflow."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from openai import AsyncOpenAI
from rich.console import Console
from rich.table import Table

from agentic_software_engineer.domain.entities.architecture_specification import ArchitectureSpecification
from agentic_software_engineer.domain.entities.code_generation_plan import GenerationReport
from agentic_software_engineer.infrastructure.di.workflow_factory import build_workflow
from agentic_software_engineer.orchestrator.state import AgenticSDLCState, WorkflowTimestamps
from agentic_software_engineer.ui.architecture_printer import ArchitecturePrinter

console = Console()


async def run() -> None:
    """Collect a requirement, execute the workflow, and render its results.

    Raises:
        RuntimeError: If the required OpenAI model configuration is absent.
    """
    user_requirement = console.input("[bold cyan]Software requirement:[/] ").strip()
    if not user_requirement:
        console.print("[bold red]A software requirement is required.[/]")
        return

    model = os.getenv("OPENAI_MODEL", "").strip()
    if not model:
        raise RuntimeError("Set the OPENAI_MODEL environment variable to an approved GPT model identifier.")

    now = datetime.now(UTC)
    execution_id = str(uuid4())
    project_root = (Path.cwd() / "generated_projects" / execution_id).resolve()
    initial_state = AgenticSDLCState(
        execution_id=execution_id,
        project_name="console-project",
        project_root=str(project_root),
        user_requirement=user_requirement,
        timestamps=WorkflowTimestamps(created_at=now, updated_at=now),
    )

    client = AsyncOpenAI()
    workflow = build_workflow(client=client, model=model, project_root=project_root)

    console.print(f"[cyan]Starting execution {execution_id}…[/]")
    workflow_result = await workflow.ainvoke(initial_state, thread_id=execution_id)
    final_state = (
        workflow_result
        if isinstance(workflow_result, AgenticSDLCState)
        else AgenticSDLCState.model_validate(workflow_result)
    )
    _render_results(final_state)


def _render_results(state: AgenticSDLCState) -> None:
    """Pretty-print the requested workflow artifacts and aggregate metrics."""
    console.print("\n[bold green]Clarified Requirements[/]")
    for requirement in state.clarified_requirements:
        console.print(f"  • {requirement}")

    tasks_table = Table(title="Tasks")
    tasks_table.add_column("ID", style="cyan")
    tasks_table.add_column("Task")
    tasks_table.add_column("Complexity")
    tasks_table.add_column("Parallel")
    for task in state.tasks:
        tasks_table.add_row(task.task_id, task.title, task.complexity, "Yes" if task.parallelizable else "No")
    console.print(tasks_table)

    dependencies_table = Table(title="Dependencies")
    dependencies_table.add_column("Predecessor", style="cyan")
    dependencies_table.add_column("Successor", style="cyan")
    dependencies_table.add_column("Type")
    for dependency in state.dependencies:
        dependencies_table.add_row(
            dependency.predecessor_task_id,
            dependency.successor_task_id,
            dependency.dependency_type,
        )
    console.print(dependencies_table)

    metrics_table = Table(title="Execution Metrics")
    metrics_table.add_column("Metric", style="cyan")
    metrics_table.add_column("Value")
    metrics_table.add_row("Status", state.execution_status.value)
    metrics_table.add_row("Elapsed time", f"{state.metrics.elapsed_time_ms} ms")
    metrics_table.add_row("Input tokens", str(state.metrics.total_input_tokens))
    metrics_table.add_row("Output tokens", str(state.metrics.total_output_tokens))
    metrics_table.add_row("Tool calls", str(state.metrics.total_tool_calls))
    metrics_table.add_row("Retries", str(state.retry_count))
    metrics_table.add_row("Estimated cost", f"${state.metrics.estimated_cost_usd:.4f}")
    console.print(metrics_table)

    if state.architecture:
        specification = ArchitectureSpecification.model_validate(state.architecture)
        ArchitecturePrinter(console).print(specification)
    else:
        console.print("\n[yellow]No architecture specification was generated.[/]")

    if state.generation_report:
        report = GenerationReport.model_validate(state.generation_report)
        generation_table = Table(title="Code Generation")
        generation_table.add_column("Outcome", style="cyan")
        generation_table.add_column("Files")
        generation_table.add_row("Generated", str(len(report.generated_files)))
        generation_table.add_row("Skipped", str(len(report.skipped_files)))
        generation_table.add_row("Failed", str(len(report.failed_files)))
        generation_table.add_row("Blocked", str(len(report.blocked_files)))
        generation_table.add_row("Validation findings", str(len(report.validation_failures)))
        console.print(generation_table)
        if report.failed_files or report.blocked_files:
            console.print(
                "[yellow]Generation did not complete. See workflow logs for safe per-file failure details.[/]"
            )


def main() -> None:
    """Run the console workflow and present operationally safe failures."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Workflow cancelled by user.[/]")
    except Exception as error:
        console.print(f"[bold red]Workflow failed:[/] {error}")


if __name__ == "__main__":
    main()
