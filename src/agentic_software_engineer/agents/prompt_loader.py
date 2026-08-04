"""Dependency-invertible prompt loading contracts for AI agents."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class PromptLoader(Protocol):
    """Load version-controlled prompt content through an injectable boundary."""

    def load(self, prompt_path: Path) -> str:
        """Return UTF-8 prompt content from the supplied path."""


class FilePromptLoader:
    """Load prompt templates from the local filesystem."""

    def load(self, prompt_path: Path) -> str:
        """Return the UTF-8 content of a prompt file."""
        return prompt_path.read_text(encoding="utf-8")
