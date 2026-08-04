"""Provider-neutral prompt registry and loading contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from agentic_software_engineer.domain.entities.code_generation_plan import FileType


class PromptDefinition(BaseModel):
    """Versioned prompt asset resolved for a specific generation concern."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: Path = Field(description="Trusted filesystem path to the version-controlled prompt.")
    version: str = Field(min_length=1, description="Prompt asset version for artifact auditability.")


class PromptRegistry(Protocol):
    """Resolve a trusted specialized prompt from a declared file type."""

    def resolve(self, file_type: FileType) -> PromptDefinition:
        """Return the registered prompt asset for the supplied file type."""


class PromptLoader(Protocol):
    """Load trusted version-controlled prompt text."""

    def load(self, prompt_path: Path) -> str:
        """Return UTF-8 prompt content for a trusted prompt path."""
