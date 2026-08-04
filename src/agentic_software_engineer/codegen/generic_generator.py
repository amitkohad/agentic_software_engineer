"""Provider-neutral, prompt-governed generator for one enterprise project file."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agentic_software_engineer.domain.entities.code_generation_plan import (
    FileSpecification,
    GeneratedArtifact,
    GenerationStatus,
)
from agentic_software_engineer.llm.client import LLMClient, LLMGenerationError
from agentic_software_engineer.prompts.contracts import PromptLoader, PromptRegistry


class GenericGeneratorConfiguration(BaseModel):
    """Bounded context limits that prevent oversized or unsafe prompt construction."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    max_architecture_context_chars: int = Field(default=20_000, ge=1, le=100_000)
    max_project_context_chars: int = Field(default=12_000, ge=1, le=100_000)
    max_existing_file_context_chars: int = Field(default=12_000, ge=1, le=100_000)
    max_dependency_context_chars: int = Field(default=24_000, ge=1, le=100_000)
    max_dependency_count: int = Field(default=20, ge=0, le=100)


class GenericCodeGenerator:
    """Generate one file through injected prompt and LLM boundaries.

    Prompt selection is based exclusively on the trusted ``FileType`` enum; a
    generated file path can never influence which prompt is loaded. The class
    keeps all content in memory and returns a ``GeneratedArtifact`` for the
    caller to validate and persist. Retry policy intentionally remains outside
    this component.
    """

    _SECRET_ASSIGNMENT = re.compile(
        r"(?im)^(\s*(?:[A-Z][A-Z0-9_]*?(?:KEY|TOKEN|SECRET|PASSWORD)|authorization)\s*[=:]\s*)([^\r\n]+)$"
    )
    _FENCED_CONTENT = re.compile(r"\A\s*```(?:[A-Za-z0-9_+.-]+)?\s*\r?\n(.*?)\r?\n?```\s*\Z", re.DOTALL)

    def __init__(
        self,
        llm_client: LLMClient,
        prompt_registry: PromptRegistry,
        prompt_loader: PromptLoader,
        *,
        configuration: GenericGeneratorConfiguration | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Create a generator with injected LLM and trusted prompt dependencies."""
        self._llm_client = llm_client
        self._prompt_registry = prompt_registry
        self._prompt_loader = prompt_loader
        self._configuration = configuration or GenericGeneratorConfiguration()
        self._logger = logger or logging.getLogger(__name__)
        self._common_prompt_path = Path(__file__).parent.parent / "prompts" / "coding" / "common.md"

    async def generate_file(
        self,
        *,
        specification: FileSpecification,
        architecture_context: str,
        project_context: str,
        dependency_context: dict[str, str],
        execution_id: str,
    ) -> GeneratedArtifact:
        """Generate one file and return its in-memory auditable artifact.

        Args:
            specification: Approved contract for exactly one target file.
            architecture_context: Approved architecture context relevant to the file.
            project_context: Safe project conventions and existing context.
            dependency_context: Contents or public contracts keyed by direct file ID.
            execution_id: Correlation identifier for safe structured logging.

        Returns:
            A generated artifact containing content, SHA-256 hash, and LLM audit metadata.

        Raises:
            LLMGenerationError: If prompt resolution, generation, or response
                normalization cannot complete safely.
        """
        try:
            specialized_prompt = self._prompt_registry.resolve(specification.file_type)
            common_prompt = self._load_prompt(self._common_prompt_path, "common")
            file_type_prompt = self._load_prompt(specialized_prompt.path, "specialized")
            system_prompt = f"{common_prompt.rstrip()}\n\n{file_type_prompt.lstrip()}"
            user_prompt = self._build_user_prompt(
                specification=specification,
                architecture_context=architecture_context,
                project_context=project_context,
                dependency_context=dependency_context,
            )
            self._logger.info(
                "Generating file through generic generator",
                extra={
                    "execution_id": execution_id,
                    "file_id": specification.id,
                    "file_type": specification.file_type.value,
                },
            )
            response = await self._llm_client.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.0,
                metadata={"execution_id": execution_id, "file_id": specification.id, "file_type": specification.file_type.value},
            )
            content = self._strip_accidental_fences(response.content)
            if not content.strip():
                raise LLMGenerationError("Code generation returned empty content.")
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            artifact = GeneratedArtifact(
                file_id=specification.id,
                path=specification.path,
                content=content,
                content_hash=content_hash,
                generated_at=self._utc_now(),
                model=response.model,
                prompt_version=specialized_prompt.version,
                validation_status=GenerationStatus.GENERATED,
                validation_errors=[],
                attempt_number=specification.retry_count + 1,
            )
            self._logger.info(
                "File generation completed",
                extra={
                    "execution_id": execution_id,
                    "file_id": specification.id,
                    "request_id": response.request_id,
                    "latency_ms": response.latency_ms,
                },
            )
            return artifact
        except LLMGenerationError:
            raise
        except Exception as error:
            self._logger.error(
                "File generation failed",
                extra={"execution_id": execution_id, "file_id": specification.id, "error_type": type(error).__name__},
            )
            raise LLMGenerationError("Code generation failed during prompt resolution or response processing.") from error

    def _build_user_prompt(
        self,
        *,
        specification: FileSpecification,
        architecture_context: str,
        project_context: str,
        dependency_context: dict[str, str],
    ) -> str:
        """Construct a bounded, secret-sanitized task prompt for exactly one file."""
        specification_payload = specification.model_dump(mode="json", exclude={"existing_file_context"})
        payload = {
            "approved_architecture": self._bound_and_sanitize(architecture_context, self._configuration.max_architecture_context_chars),
            "file_specification": specification_payload,
            "existing_file_context": self._bound_and_sanitize(
                specification.existing_file_context or "",
                self._configuration.max_existing_file_context_chars,
            ),
            "direct_dependencies": self._direct_dependencies(specification, dependency_context),
            "project_conventions": self._bound_and_sanitize(project_context, self._configuration.max_project_context_chars),
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def _direct_dependencies(self, specification: FileSpecification, dependency_context: dict[str, str]) -> dict[str, str]:
        """Return only approved direct dependency context within configured size limits."""
        dependencies: dict[str, str] = {}
        remaining = self._configuration.max_dependency_context_chars
        for dependency_id in specification.dependencies[: self._configuration.max_dependency_count]:
            if self._is_environment_file(dependency_id):
                continue
            raw_context = dependency_context.get(dependency_id)
            if raw_context is None or remaining <= 0:
                continue
            safe_context = self._bound_and_sanitize(raw_context, remaining)
            dependencies[dependency_id] = safe_context
            remaining -= len(safe_context)
        return dependencies

    def _load_prompt(self, prompt_path: Path, prompt_kind: str) -> str:
        """Load non-empty trusted prompt content without exposing it through logs."""
        prompt = self._prompt_loader.load(prompt_path).strip()
        if not prompt:
            raise LLMGenerationError(f"Configured {prompt_kind} coding prompt is empty.")
        return prompt

    def _bound_and_sanitize(self, value: str, maximum_length: int) -> str:
        """Remove secret-like assignments and enforce a deterministic context limit."""
        if self._is_environment_file(value):
            return "[Context omitted because it may contain environment-file content.]"
        sanitized = self._SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", value)
        if len(sanitized) <= maximum_length:
            return sanitized
        return f"{sanitized[:maximum_length]}\n[Context truncated to configured safety limit.]"

    @classmethod
    def _is_environment_file(cls, value: str) -> bool:
        """Return whether a path or context marker indicates an environment file."""
        normalized = value.casefold()
        return ".env" in normalized and ("\n" not in value or "=" in value)

    @classmethod
    def _strip_accidental_fences(cls, content: str) -> str:
        """Remove only one outer Markdown fence while preserving valid code unchanged."""
        match = cls._FENCED_CONTENT.fullmatch(content)
        return match.group(1) if match else content

    @staticmethod
    def _utc_now() -> datetime:
        """Return a timezone-aware UTC timestamp without importing provider state."""
        return datetime.now(UTC)
