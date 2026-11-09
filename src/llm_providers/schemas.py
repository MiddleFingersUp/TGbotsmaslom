"""Normalized, provider-agnostic LLM request/response models.

Per docs/DEVELOPMENT_PLAN.md Stage 4 and docs/ARCHITECTURE.md §8:
the rest of the application only sees these Pydantic models — never a raw
vendor response. Missing data is represented explicitly as `None`;
token counts are never fabricated.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MessageRole = Literal["system", "user", "assistant"]


class LLMMessage(BaseModel):
    """A single chat message in the standardized conversation format."""

    model_config = ConfigDict(frozen=True)

    role: MessageRole
    content: str = Field(min_length=1)


class TokenUsage(BaseModel):
    """Token consumption reported by a provider.

    `None` means the provider did not return usage data for the corresponding
    counter — absence is explicit and never replaced by guessed values.
    """

    model_config = ConfigDict(frozen=True)

    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)

    @property
    def is_available(self) -> bool:
        """True when the provider reported at least one token counter."""
        return any(
            v is not None for v in (self.prompt_tokens, self.completion_tokens, self.total_tokens)
        )


class LLMRequest(BaseModel):
    """Validated input for a provider `complete()` call."""

    model_config = ConfigDict(frozen=True)

    messages: list[LLMMessage] = Field(min_length=1)
    system_prompt: str | None = None
    model: str | None = None  # None → provider default model from configuration
    temperature: float = Field(default=0.5, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    timeout: float | None = Field(default=None, ge=1.0)  # per-request override (seconds)
    extra_params: dict[str, Any] = Field(default_factory=dict)

    def compiled_messages(self) -> list[dict[str, str]]:
        """Return the message list with the optional system prompt merged first."""
        compiled: list[dict[str, str]] = []
        if self.system_prompt:
            compiled.append({"role": "system", "content": self.system_prompt})
        compiled.extend({"role": m.role, "content": m.content} for m in self.messages)
        return compiled


class LLMResponse(BaseModel):
    """Normalized provider response returned to the application layer."""

    model_config = ConfigDict(frozen=True)

    content: str
    model: str
    provider: str
    finish_reason: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    request_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content")
    @classmethod
    def content_must_be_string(cls, value: str) -> str:
        """Enforce that content is a plain string (may be empty only if provider returned it)."""
        return value
