"""Deterministic validation of individual generated source artifacts."""

from __future__ import annotations

import ast
import json
import re
from enum import StrEnum
from pathlib import PurePath

from pydantic import BaseModel, ConfigDict, Field

from agentic_software_engineer.domain.entities.code_generation_plan import FileSpecification, GeneratedArtifact


class IssueSeverity(StrEnum):
    """Severity assigned to a deterministic code-validation finding."""

    WARNING = "warning"
    ERROR = "error"


class ValidationIssue(BaseModel):
    """One actionable validation finding for a generated artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    rule: str = Field(min_length=1, description="Stable validation rule identifier.")
    severity: IssueSeverity = Field(description="Impact level of the finding.")
    message: str = Field(min_length=1, description="Actionable explanation of the finding.")
    line_number: int | None = Field(default=None, ge=1, description="One-based source line when known.")
    blocking: bool = Field(description="Whether the issue makes the artifact invalid.")


class CodeValidationResult(BaseModel):
    """Complete, JSON-serializable validation outcome for one generated file."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    valid: bool = Field(description="Whether no blocking issues were found.")
    syntax_valid: bool = Field(description="Whether the file parsed for its detected format.")
    issues: list[ValidationIssue] = Field(default_factory=list, description="All validation findings.")
    warnings: list[str] = Field(default_factory=list, description="Messages for non-blocking findings.")
    defined_symbols: list[str] = Field(default_factory=list, description="Classes, functions, and assigned names found in source.")
    imported_symbols: list[str] = Field(default_factory=list, description="Imported modules and symbols found in source.")
    score: int = Field(ge=0, le=100, description="Deterministic quality score from zero to one hundred.")


class CodeValidator:
    """Validate generated content without executing it or using an LLM."""

    _BANNED_TEXT_PATTERNS = (
        ("banned_eval", re.compile(r"\beval\s*\("), "Use of eval() is prohibited."),
        ("banned_exec", re.compile(r"\bexec\s*\("), "Use of exec() is prohibited."),
        ("unsafe_shell", re.compile(r"\bshell\s*=\s*True\b"), "shell=True is prohibited."),
        (
            "hardcoded_secret",
            re.compile(
                r"(?im)^\s*(?:[A-Z][A-Z0-9_]*?(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD)|api[_-]?key|token|secret|password)\s*=\s*['\"][^'\"]+['\"]"
            ),
            "Hardcoded API-key-like assignment is prohibited.",
        ),
    )

    def validate(self, artifact: GeneratedArtifact, specification: FileSpecification) -> CodeValidationResult:
        """Validate one generated artifact against its file specification.

        Args:
            artifact: In-memory generated content and generation metadata.
            specification: Approved contract for the target file.

        Returns:
            All deterministic validation findings and extracted source symbols.
        """
        issues: list[ValidationIssue] = []
        defined_symbols: list[str] = []
        imported_symbols: list[str] = []
        content = artifact.content
        suffix = PurePath(specification.path).suffix.casefold()
        filename = PurePath(specification.path).name.casefold()
        syntax_valid = True

        if not content.strip():
            issues.append(self._issue("empty_content", IssueSeverity.ERROR, "Generated file content is empty.", blocking=True))
            syntax_valid = False

        if suffix != ".md" and re.search(r"(?m)^\s*```", content):
            issues.append(self._issue("markdown_fence", IssueSeverity.ERROR, "Generated source must not contain Markdown code fences.", blocking=True))

        if suffix == ".py":
            syntax_valid = self._validate_python(content, specification, issues, defined_symbols, imported_symbols) and syntax_valid
        elif suffix == ".json":
            syntax_valid = self._validate_json(content, issues) and syntax_valid
        elif suffix in {".yaml", ".yml"}:
            syntax_valid = self._validate_yaml(content, issues) and syntax_valid
        elif filename == "dockerfile":
            self._validate_dockerfile(content, issues)
        elif suffix == ".md":
            self._validate_markdown(content, issues)
        else:
            self._validate_basic_content(content, issues)

        warnings = [issue.message for issue in issues if issue.severity is IssueSeverity.WARNING]
        blocking_issues = [issue for issue in issues if issue.blocking]
        score = max(0, 100 - sum(25 if issue.blocking else 5 for issue in issues))
        return CodeValidationResult(
            valid=not blocking_issues,
            syntax_valid=syntax_valid,
            issues=issues,
            warnings=warnings,
            defined_symbols=sorted(set(defined_symbols)),
            imported_symbols=sorted(set(imported_symbols)),
            score=score,
        )

    def _validate_python(
        self,
        content: str,
        specification: FileSpecification,
        issues: list[ValidationIssue],
        defined_symbols: list[str],
        imported_symbols: list[str],
    ) -> bool:
        """Parse Python, extract symbols/imports, and apply Python safety rules."""
        self._validate_banned_patterns(content, issues)
        try:
            module = ast.parse(content, filename=specification.path)
        except SyntaxError as error:
            issues.append(
                self._issue(
                    "python_syntax",
                    IssueSeverity.ERROR,
                    f"Python syntax error: {error.msg}.",
                    line_number=error.lineno,
                    blocking=True,
                )
            )
            return False

        defined_symbols.extend(self._defined_symbols(module))
        imported_symbols.extend(self._imported_symbols(module))
        for expected_symbol in specification.symbols_to_define:
            if expected_symbol not in defined_symbols:
                issues.append(
                    self._issue(
                        "missing_symbol",
                        IssueSeverity.ERROR,
                        f"Required symbol '{expected_symbol}' is not defined.",
                        blocking=True,
                    )
                )
        for handler in (node for node in ast.walk(module) if isinstance(node, ast.ExceptHandler)):
            if handler.type is None:
                issues.append(
                    self._issue(
                        "bare_except",
                        IssueSeverity.ERROR,
                        "Broad bare except clause is prohibited.",
                        line_number=handler.lineno,
                        blocking=True,
                    )
                )
        if not self._placeholders_allowed(specification):
            self._validate_placeholders(content, module, issues)
        return True

    def _validate_json(self, content: str, issues: list[ValidationIssue]) -> bool:
        """Parse JSON content and report a blocking parse failure when invalid."""
        try:
            json.loads(content)
            return True
        except json.JSONDecodeError as error:
            issues.append(self._issue("json_syntax", IssueSeverity.ERROR, f"JSON syntax error: {error.msg}.", line_number=error.lineno, blocking=True))
            return False

    def _validate_yaml(self, content: str, issues: list[ValidationIssue]) -> bool:
        """Parse YAML safely when PyYAML is installed; otherwise emit a warning."""
        try:
            import yaml
        except ImportError:
            issues.append(self._issue("yaml_validator_unavailable", IssueSeverity.WARNING, "PyYAML is not installed; YAML syntax was not validated.", blocking=False))
            return True
        try:
            yaml.safe_load(content)
            return True
        except yaml.YAMLError as error:
            mark = getattr(error, "problem_mark", None)
            line_number = mark.line + 1 if mark is not None else None
            issues.append(self._issue("yaml_syntax", IssueSeverity.ERROR, "YAML syntax error.", line_number=line_number, blocking=True))
            return False

    def _validate_dockerfile(self, content: str, issues: list[ValidationIssue]) -> None:
        """Apply minimum deterministic Dockerfile content checks."""
        if not content.strip():
            issues.append(self._issue("dockerfile_empty", IssueSeverity.ERROR, "Dockerfile must not be empty.", blocking=True))
        elif not re.search(r"(?im)^\s*FROM\s+\S+", content):
            issues.append(self._issue("dockerfile_base_image", IssueSeverity.ERROR, "Dockerfile must declare a FROM base image.", blocking=True))

    def _validate_markdown(self, content: str, issues: list[ValidationIssue]) -> None:
        """Apply minimum deterministic Markdown documentation checks."""
        if not content.strip():
            issues.append(self._issue("markdown_empty", IssueSeverity.ERROR, "Markdown file must not be empty.", blocking=True))
        elif not re.search(r"(?m)^#{1,6}\s+\S+", content):
            issues.append(self._issue("markdown_heading", IssueSeverity.WARNING, "Markdown file has no heading.", blocking=False))

    def _validate_basic_content(self, content: str, issues: list[ValidationIssue]) -> None:
        """Apply basic non-empty validation to unsupported file formats."""
        if not content.strip():
            issues.append(self._issue("empty_content", IssueSeverity.ERROR, "Generated file content is empty.", blocking=True))

    def _validate_banned_patterns(self, content: str, issues: list[ValidationIssue]) -> None:
        """Find banned text patterns and preserve all matching line locations."""
        for rule, pattern, message in self._BANNED_TEXT_PATTERNS:
            for match in pattern.finditer(content):
                line_number = content.count("\n", 0, match.start()) + 1
                issues.append(self._issue(rule, IssueSeverity.ERROR, message, line_number=line_number, blocking=True))

    def _validate_placeholders(self, content: str, module: ast.Module, issues: list[ValidationIssue]) -> None:
        """Reject placeholder-only function or class bodies when not explicitly allowed."""
        if re.search(r"(?im)^\s*#\s*TODO\b", content) and self._source_is_placeholder_only(content):
            issues.append(self._issue("placeholder_todo", IssueSeverity.ERROR, "Artifact contains only TODO placeholder content.", blocking=True))
        for node in ast.walk(module):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and self._is_placeholder_body(node.body):
                issues.append(
                    self._issue(
                        "placeholder_implementation",
                        IssueSeverity.ERROR,
                        f"'{node.name}' contains only a placeholder implementation.",
                        line_number=node.lineno,
                        blocking=True,
                    )
                )

    @staticmethod
    def _defined_symbols(module: ast.Module) -> list[str]:
        """Extract declared classes, functions, and assignment targets from Python AST."""
        symbols: list[str] = []
        for node in ast.walk(module):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(node.name)
            elif isinstance(node, ast.Assign):
                symbols.extend(target.id for target in node.targets if isinstance(target, ast.Name))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                symbols.append(node.target.id)
        return symbols

    @staticmethod
    def _imported_symbols(module: ast.Module) -> list[str]:
        """Extract imported module and symbol identifiers from Python AST."""
        symbols: list[str] = []
        for node in ast.walk(module):
            if isinstance(node, ast.Import):
                symbols.extend(alias.asname or alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                prefix = "." * node.level + (node.module or "")
                symbols.extend(f"{prefix}:{alias.asname or alias.name}" for alias in node.names)
        return symbols

    @staticmethod
    def _is_placeholder_body(body: list[ast.stmt]) -> bool:
        """Return whether a body has only pass, docstring, or NotImplementedError statements."""
        meaningful_statements = [
            statement
            for statement in body
            if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str))
        ]
        if not meaningful_statements:
            return True
        return all(
            isinstance(statement, ast.Pass)
            or (
                isinstance(statement, ast.Raise)
                and isinstance(statement.exc, ast.Call)
                and isinstance(statement.exc.func, ast.Name)
                and statement.exc.func.id == "NotImplementedError"
            )
            for statement in meaningful_statements
        )

    @staticmethod
    def _source_is_placeholder_only(content: str) -> bool:
        """Return whether meaningful source lines consist only of TODO or pass placeholders."""
        meaningful_lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith('"""')]
        return all(line == "pass" or line.casefold().startswith("# todo") for line in meaningful_lines)

    @staticmethod
    def _placeholders_allowed(specification: FileSpecification) -> bool:
        """Check explicit validation rules for permission to emit a placeholder implementation."""
        rules = " ".join(specification.validation_rules).casefold()
        return "allow placeholder" in rules or "placeholders allowed" in rules

    @staticmethod
    def _issue(
        rule: str,
        severity: IssueSeverity,
        message: str,
        *,
        line_number: int | None = None,
        blocking: bool,
    ) -> ValidationIssue:
        """Create a consistently typed validation issue."""
        return ValidationIssue(rule=rule, severity=severity, message=message, line_number=line_number, blocking=blocking)
