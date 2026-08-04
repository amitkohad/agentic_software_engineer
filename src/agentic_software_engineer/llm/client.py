"""Provider-neutral contracts for large-language-model generation."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class LLMResponse(BaseModel):
    """Immutable normalized result returned by an LLM generation request."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    content: str = Field(min_length=1, description="Generated response content.")
    model: str = Field(min_length=1, description="Model identifier that generated the response.")
    input_tokens: int = Field(ge=0, description="Input token count reported by the provider.")
    output_tokens: int = Field(ge=0, description="Output token count reported by the provider.")
    request_id: str = Field(min_length=1, description="Provider-agnostic generation request identifier.")
    latency_ms: int = Field(ge=0, description="End-to-end generation latency in milliseconds.")
    finish_reason: str = Field(min_length=1, description="Normalized provider completion reason.")


class LLMGenerationError(Exception):
    """Raised when an LLM generation operation cannot produce a valid response."""


class LLMClient(Protocol):
    """Asynchronous provider-neutral interface used by agents that require text generation."""

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        metadata: dict[str, str] | None = None,
    ) -> LLMResponse:
        """Generate a normalized response from system and user prompts.

        Args:
            system_prompt: Governing instruction for the requested generation.
            user_prompt: Task-specific prompt and structured context.
            temperature: Requested sampling temperature from zero to one.
            metadata: Non-sensitive correlation metadata for observability.

        Returns:
            A complete, normalized LLM response.

        Raises:
            LLMGenerationError: If generation fails or a valid response cannot be returned.
        """
