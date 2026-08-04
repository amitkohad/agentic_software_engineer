"""Trusted file-type to coding-prompt registry used by code generation."""

from __future__ import annotations

import logging
from pathlib import Path
from threading import RLock

from agentic_software_engineer.domain.entities.code_generation_plan import FileType
from agentic_software_engineer.prompts.contracts import PromptDefinition


class PromptNotRegisteredError(KeyError):
    """Raised when a generation file type has no registered prompt asset."""


class FilePromptRegistry:
    """Thread-safe registry of approved prompt assets keyed by ``FileType``."""

    _DEFAULT_FILENAMES: dict[FileType, str] = {
        FileType.API: "api.md",
        FileType.DOMAIN: "domain.md",
        FileType.REPOSITORY: "repository.md",
        FileType.CONFIGURATION: "configuration.md",
        FileType.TEST: "test.md",
        FileType.DOCUMENTATION: "documentation.md",
        FileType.MIGRATION: "repository.md",
        FileType.INFRASTRUCTURE: "configuration.md",
    }

    def __init__(self, prompt_root: Path, *, logger: logging.Logger | None = None) -> None:
        """Register the repository-owned default prompts beneath ``prompt_root``."""
        self._definitions = {
            file_type: PromptDefinition(path=prompt_root / filename, version="1")
            for file_type, filename in self._DEFAULT_FILENAMES.items()
        }
        self._lock = RLock()
        self._logger = logger or logging.getLogger(__name__)

    def resolve(self, file_type: FileType) -> PromptDefinition:
        """Return the trusted prompt definition registered for ``file_type``."""
        with self._lock:
            definition = self._definitions.get(file_type)
        if definition is None:
            raise PromptNotRegisteredError(f"No coding prompt is registered for file type '{file_type.value}'.")
        return definition

    def register(self, file_type: FileType, definition: PromptDefinition, *, replace: bool = False) -> None:
        """Register an approved prompt definition, rejecting unintended replacement."""
        with self._lock:
            if file_type in self._definitions and not replace:
                raise ValueError(f"A coding prompt is already registered for '{file_type.value}'.")
            self._definitions[file_type] = definition
        self._logger.info("Coding prompt registered", extra={"file_type": file_type.value, "path": str(definition.path)})
