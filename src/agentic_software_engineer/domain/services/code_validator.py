"""Deterministic static validation for generated Python projects."""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agentic_software_engineer.domain.entities.code_generation_plan import CodeGenerationPlan


class CodeValidationReport(BaseModel):
    """Structured, reproducible result of code-generation validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    errors: list[str] = Field(default_factory=list, description="Blocking validation failures.")
    warnings: list[str] = Field(default_factory=list, description="Non-blocking code quality concerns.")
    recommendations: list[str] = Field(default_factory=list, description="Concrete remediation guidance.")
    score: int = Field(ge=0, le=100, description="Deterministic project quality score from zero to one hundred.")


class CodeValidator:
    """Validate generated Python code using AST analysis and plan metadata only.

    The validator performs no code execution. Scores begin at 100; each error
    deducts 15 points and each warning deducts 5 points, bounded at zero. The
    stable rule ordering makes the report appropriate for CI quality gates.
    """

    _ERROR_DEDUCTION = 15
    _WARNING_DEDUCTION = 5

    def __init__(self, max_line_length: int = 120) -> None:
        """Create a validator with a configurable deterministic line-length limit."""
        if max_line_length < 1:
            raise ValueError("max_line_length must be positive.")
        self._max_line_length = max_line_length

    def validate(self, project_root: Path, plan: CodeGenerationPlan) -> CodeValidationReport:
        """Validate a generated project against its approved generation plan.

        Args:
            project_root: Root directory containing generated project artifacts.
            plan: Approved plan declaring expected files, packages, and layout.

        Returns:
            Typed errors, warnings, recommendations, and deterministic score.
        """
        errors: list[str] = []
        warnings: list[str] = []
        recommendations: list[str] = []
        root = project_root.resolve()
        python_files = self._python_files(root, plan, errors)
        parsed_modules = self._parse_modules(python_files, errors)
        module_paths = self._module_paths(root, parsed_modules)

        self._validate_project_structure(root, plan, errors, warnings, recommendations)
        self._validate_formatting(python_files, warnings, recommendations)
        self._validate_type_hints(parsed_modules, warnings, recommendations)
        self._validate_expected_classes(plan, parsed_modules, warnings, recommendations)
        dependency_graph = self._validate_imports(parsed_modules, module_paths, plan, errors, warnings, recommendations)
        self._validate_circular_imports(dependency_graph, errors, recommendations)

        score = max(0, 100 - len(errors) * self._ERROR_DEDUCTION - len(warnings) * self._WARNING_DEDUCTION)
        return CodeValidationReport(errors=errors, warnings=warnings, recommendations=recommendations, score=score)

    @staticmethod
    def _python_files(root: Path, plan: CodeGenerationPlan, errors: list[str]) -> list[Path]:
        """Return planned Python files that exist, recording missing planned files."""
        files: list[Path] = []
        for specification in plan.files:
            if not specification.path.endswith(".py"):
                continue
            path = (root / specification.path).resolve()
            if root not in path.parents:
                errors.append(f"Planned Python file escapes project root: '{specification.path}'.")
            elif not path.is_file():
                errors.append(f"Planned Python file is missing: '{specification.path}'.")
            else:
                files.append(path)
        return files

    @staticmethod
    def _parse_modules(python_files: Iterable[Path], errors: list[str]) -> dict[Path, ast.Module]:
        """Parse Python modules and record syntax failures without execution."""
        modules: dict[Path, ast.Module] = {}
        for path in python_files:
            try:
                modules[path] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError as error:
                errors.append(f"Python syntax error in '{path.name}' at line {error.lineno}: {error.msg}.")
        return modules

    @staticmethod
    def _module_paths(root: Path, modules: dict[Path, ast.Module]) -> dict[str, Path]:
        """Map discovered project Python files to their canonical import module names."""
        module_paths: dict[str, Path] = {}
        for path in modules:
            relative = path.relative_to(root).with_suffix("")
            parts = list(relative.parts)
            if parts[-1] == "__init__":
                parts.pop()
            if parts:
                module_paths[".".join(parts)] = path
        return module_paths

    @staticmethod
    def _validate_project_structure(
        root: Path,
        plan: CodeGenerationPlan,
        errors: list[str],
        warnings: list[str],
        recommendations: list[str],
    ) -> None:
        """Validate planned directories and flag unexpected Python source files."""
        for directory in sorted({Path(specification.path).parent for specification in plan.files if str(Path(specification.path).parent) != "."}):
            target = (root / directory).resolve()
            if root not in target.parents and target != root:
                errors.append(f"Planned directory escapes project root: '{directory}'.")
            elif not target.is_dir():
                errors.append(f"Planned directory is missing: '{directory}'.")

        planned_paths = {specification.path for specification in plan.files}
        for path in root.rglob("*.py"):
            relative_path = path.relative_to(root).as_posix()
            if relative_path not in planned_paths:
                warnings.append(f"Unplanned Python file found: '{relative_path}'.")
                recommendations.append("Add all maintained Python files to the CodeGenerationPlan inventory.")

    def _validate_formatting(
        self,
        python_files: Iterable[Path],
        warnings: list[str],
        recommendations: list[str],
    ) -> None:
        """Apply deterministic baseline formatting checks without a formatter dependency."""
        formatting_issues_found = False
        for path in python_files:
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if "\t" in line:
                    warnings.append(f"Tab indentation found in '{path.name}' line {line_number}.")
                    formatting_issues_found = True
                if line.rstrip() != line:
                    warnings.append(f"Trailing whitespace found in '{path.name}' line {line_number}.")
                    formatting_issues_found = True
                if len(line) > self._max_line_length:
                    warnings.append(f"Line exceeds {self._max_line_length} characters in '{path.name}' line {line_number}.")
                    formatting_issues_found = True
        if formatting_issues_found:
            recommendations.append("Run the approved formatter and remove trailing whitespace before release.")

    @staticmethod
    def _validate_type_hints(
        modules: dict[Path, ast.Module],
        warnings: list[str],
        recommendations: list[str],
    ) -> None:
        """Flag public functions whose parameters or return values lack annotations."""
        missing_hints_found = False
        for path, module in modules.items():
            for node in ast.walk(module):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name.startswith("_"):
                    continue
                arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                if node.args.vararg is not None:
                    arguments.append(node.args.vararg)
                if node.args.kwarg is not None:
                    arguments.append(node.args.kwarg)
                missing = [argument.arg for argument in arguments if argument.arg not in {"self", "cls"} and argument.annotation is None]
                if missing or node.returns is None:
                    detail = ", ".join(missing) if missing else "return type"
                    warnings.append(f"Public function '{node.name}' in '{path.name}' has missing type hints: {detail}.")
                    missing_hints_found = True
        if missing_hints_found:
            recommendations.append("Add complete parameter and return type hints to public functions.")

    @staticmethod
    def _validate_expected_classes(
        plan: CodeGenerationPlan,
        modules: dict[Path, ast.Module],
        warnings: list[str],
        recommendations: list[str],
    ) -> None:
        """Check class-like public interfaces declared by the generation plan."""
        declared_classes = {node.name for module in modules.values() for node in ast.walk(module) if isinstance(node, ast.ClassDef)}
        expected_classes = {
            symbol
            for file_specification in plan.files
            for symbol in file_specification.symbols_to_define
            if symbol.isidentifier() and symbol[:1].isupper()
        }
        for class_name in sorted(expected_classes - declared_classes):
            warnings.append(f"Expected public class '{class_name}' is missing.")
        if expected_classes - declared_classes:
            recommendations.append("Generate or document the public classes declared by module interfaces.")

    def _validate_imports(
        self,
        modules: dict[Path, ast.Module],
        module_paths: dict[str, Path],
        plan: CodeGenerationPlan,
        errors: list[str],
        warnings: list[str],
        recommendations: list[str],
    ) -> dict[Path, set[Path]]:
        """Resolve declared imports and construct the internal dependency graph."""
        graph: dict[Path, set[Path]] = defaultdict(set)
        declared_packages = {package.name.casefold().replace("-", "_") for package in plan.external_packages}
        used_external_packages: set[str] = set()
        standard_library = getattr(sys, "stdlib_module_names", frozenset())

        for path, module in modules.items():
            for imported_module in self._imports_for(module, path, module_paths):
                target_path = self._resolve_internal_import(imported_module, module_paths)
                root_name = imported_module.split(".", maxsplit=1)[0].casefold().replace("-", "_")
                if target_path is not None:
                    graph[path].add(target_path)
                elif root_name in standard_library or root_name in {"__future__", "typing"}:
                    continue
                elif root_name in declared_packages:
                    used_external_packages.add(root_name)
                else:
                    errors.append(f"Unresolved import '{imported_module}' in '{path.name}'.")

        for package in sorted(declared_packages - used_external_packages):
            warnings.append(f"Declared external package '{package}' is not imported by planned Python files.")
        if declared_packages - used_external_packages:
            recommendations.append("Remove unused packages or document their runtime-only dependency purpose.")
        return graph

    @staticmethod
    def _imports_for(module: ast.Module, path: Path, module_paths: dict[str, Path]) -> Iterable[str]:
        """Yield absolute module names for import statements using deterministic resolution."""
        current_module = next((name for name, candidate in module_paths.items() if candidate == path), "")
        current_parts = current_module.split(".")[:-1]
        for node in ast.walk(module):
            if isinstance(node, ast.Import):
                yield from (alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base_parts = current_parts[: max(0, len(current_parts) - max(0, node.level - 1))] if node.level else []
                module_name = node.module or ""
                base_module = ".".join([*base_parts, module_name]).strip(".") if node.level else module_name
                if base_module:
                    yield base_module
                for alias in node.names:
                    candidate = f"{base_module}.{alias.name}" if base_module else alias.name
                    if candidate in module_paths:
                        yield candidate

    @staticmethod
    def _resolve_internal_import(imported_module: str, module_paths: dict[str, Path]) -> Path | None:
        """Resolve an import to the most specific known project module path."""
        candidate = imported_module
        while candidate:
            if candidate in module_paths:
                return module_paths[candidate]
            candidate = candidate.rpartition(".")[0]
        return None

    @staticmethod
    def _validate_circular_imports(
        graph: dict[Path, set[Path]],
        errors: list[str],
        recommendations: list[str],
    ) -> None:
        """Detect directed cycles in the internal import graph using depth-first search."""
        visited: set[Path] = set()
        active: list[Path] = []
        reported: set[tuple[str, ...]] = set()

        def visit(path: Path) -> None:
            if path in active:
                cycle = tuple(item.name for item in [*active[active.index(path) :], path])
                if cycle not in reported:
                    reported.add(cycle)
                    errors.append(f"Circular import detected: {' -> '.join(cycle)}.")
                return
            if path in visited:
                return
            visited.add(path)
            active.append(path)
            for dependency in sorted(graph.get(path, set()), key=lambda item: str(item)):
                visit(dependency)
            active.pop()

        for module_path in sorted(graph, key=lambda item: str(item)):
            visit(module_path)
        if reported:
            recommendations.append("Break circular imports by inverting dependencies or extracting shared contracts.")
