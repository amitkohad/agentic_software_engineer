"""Official OpenAI SDK adapter for the provider-neutral LLM client contract."""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)
from pydantic import BaseModel, ConfigDict, Field

from agentic_software_engineer.llm.client import LLMClient, LLMGenerationError, LLMResponse


class OpenAIClientConfiguration(BaseModel):
    """Injected, non-secret operational configuration for the OpenAI adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    model: str = Field(min_length=1, description="Approved model identifier selected by application configuration.")
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=600, description="Per-request timeout in seconds.")
    max_attempts: int = Field(default=3, ge=1, le=5, description="Maximum total attempts for transient failures.")
    initial_retry_delay_seconds: float = Field(default=0.5, gt=0, le=30, description="Initial exponential retry delay in seconds.")


class OpenAILLMClient(LLMClient):
    """Asynchronous OpenAI adapter with bounded transient-error recovery.

    The SDK client and operational configuration are injected by the composition
    root. This adapter deliberately never logs prompts, generated output,
    metadata values, API keys, source code, or raw provider exception messages.
    """

    def __init__(
        self,
        client: AsyncOpenAI,
        configuration: OpenAIClientConfiguration,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        """Create the adapter with injected SDK client, configuration, and logger."""
        self._client = client
        self._configuration = configuration
        self._logger = logger or logging.getLogger(__name__)

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        metadata: dict[str, str] | None = None,
    ) -> LLMResponse:
        """Generate text through the OpenAI Responses API.

        Only connection, timeout, rate-limit, server, and transient HTTP-status
        failures are retried. Authentication, authorization, invalid-request,
        and validation failures are never retried.

        Args:
            system_prompt: System instruction passed to the provider.
            user_prompt: Task-specific user instruction passed to the provider.
            temperature: Sampling temperature from zero through two.
            metadata: Optional non-sensitive correlation metadata.

        Returns:
            Normalized provider response with usage and latency where available.

        Raises:
            LLMGenerationError: If the request cannot produce a valid response.
        """
        if not 0.0 <= temperature <= 2.0:
            raise LLMGenerationError("Generation request rejected: temperature must be between 0.0 and 2.0.")
        if not system_prompt or not user_prompt:
            raise LLMGenerationError("Generation request rejected: system and user prompts must be non-empty.")

        started_at = perf_counter()
        for attempt in range(1, self._configuration.max_attempts + 1):
            try:
                response = await self._client.responses.create(
                    model=self._configuration.model,
                    instructions=system_prompt,
                    input=user_prompt,
                    temperature=temperature,
                    metadata=metadata,
                    timeout=self._configuration.request_timeout_seconds,
                )
                latency_ms = int((perf_counter() - started_at) * 1_000)
                return self._to_llm_response(response, latency_ms)
            except Exception as error:
                transient = self._is_transient(error)
                has_remaining_attempts = attempt < self._configuration.max_attempts
                if transient and has_remaining_attempts:
                    delay = self._configuration.initial_retry_delay_seconds * (2 ** (attempt - 1))
                    self._logger.warning(
                        "Transient LLM request failure; retrying",
                        extra={"model": self._configuration.model, "attempt": attempt, "retry_delay_seconds": delay},
                    )
                    await asyncio.sleep(delay)
                    continue

                error_category = self._error_category(error)
                self._logger.error(
                    "LLM generation failed",
                    extra={"model": self._configuration.model, "attempt": attempt, "error_category": error_category},
                )
                raise LLMGenerationError(
                    f"LLM generation failed: category={error_category}; attempts={attempt}."
                ) from error

        raise LLMGenerationError("LLM generation failed: retry loop exited unexpectedly.")

    def _to_llm_response(self, response: Any, latency_ms: int) -> LLMResponse:
        """Normalize an SDK response without retaining provider-specific response objects."""
        content = getattr(response, "output_text", "")
        if not isinstance(content, str) or not content.strip():
            raise LLMGenerationError("LLM generation failed: provider returned empty content.")
        usage = getattr(response, "usage", None)
        request_id = getattr(response, "_request_id", None) or getattr(response, "id", None) or "unavailable"
        return LLMResponse(
            content=content,
            model=str(getattr(response, "model", self._configuration.model)),
            input_tokens=self._usage_value(usage, "input_tokens"),
            output_tokens=self._usage_value(usage, "output_tokens"),
            request_id=str(request_id),
            latency_ms=latency_ms,
            finish_reason=str(getattr(response, "status", "completed")),
        )

    @staticmethod
    def _usage_value(usage: Any, field_name: str) -> int:
        """Read a non-negative integer usage field defensively from an SDK response."""
        value = getattr(usage, field_name, 0) if usage is not None else 0
        return value if isinstance(value, int) and value >= 0 else 0

    @staticmethod
    def _is_transient(error: Exception) -> bool:
        """Return whether an error is safe for bounded retry under the adapter policy."""
        if isinstance(error, (AuthenticationError, PermissionDeniedError, BadRequestError, UnprocessableEntityError)):
            return False
        if isinstance(error, (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)):
            return True
        return isinstance(error, APIStatusError) and error.status_code in {408, 409, 425, 429} | set(range(500, 600))

    @staticmethod
    def _error_category(error: Exception) -> str:
        """Map provider exceptions to sanitized, non-sensitive categories."""
        if isinstance(error, AuthenticationError):
            return "authentication"
        if isinstance(error, PermissionDeniedError):
            return "authorization"
        if isinstance(error, (BadRequestError, UnprocessableEntityError)):
            return "invalid_request"
        if isinstance(error, APITimeoutError):
            return "timeout"
        if isinstance(error, APIConnectionError):
            return "connection"
        if isinstance(error, RateLimitError):
            return "rate_limited"
        if isinstance(error, InternalServerError):
            return "server"
        if isinstance(error, APIStatusError):
            return "http_status"
        return "unexpected"
