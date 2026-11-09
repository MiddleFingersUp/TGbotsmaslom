"""Normalized, provider-agnostic LLM request/response models.

Per docs/DEVELOPMENT_PLAN.md Stage 4 and docs/ARCHITECTURE.md §8:
the rest of the application only sees these Pydantic models — never a raw
vendor response. Missing data is represented explicitly as `None`;
token counts are never fabricated.

Since Stage 5.2 the conversation message format also supports the `tool`
role (tool results fed back into the loop) and assistant messages carrying
the vendor-shaped `tool_calls` they asked for — both additive and ignored
when not used.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MessageRole = Literal["system", "user", "assistant", "tool"]


class LLMMessage(BaseModel):
    """A single chat message in the standardized conversation format.

    `role="tool"` carries a tool result and MUST provide `tool_call_id`;
    `role="assistant"` may reference the tool calls it requested via
    `tool_calls` (its content may then legitimately be empty).
    """

    model_config = ConfigDict(frozen=True)

    role: MessageRole
    content: str
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] | None = None

    @model_validator(mode="after")
    def content_rules(self) -> LLMMessage:
        """Content may be empty ONLY for assistant messages with tool calls."""
        if not self.content:
            if self.role == "assistant" and self.tool_calls:
                return self
            raise ValueError("content must not be empty for this message.")
        return self

    def to_compiled_dict(self) -> dict[str, Any]:
        """Vendor-shaped message dict; optional fields omitted when unset."""
        entry: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_call_id is not None:
            entry["tool_call_id"] = self.tool_call_id
        if self.tool_calls is not None:
            entry["tool_calls"] = [call.to_vendor_format() for call in self.tool_calls]
        return entry


class ToolSpec(BaseModel):
    """Provider-agnostic tool description sent to the LLM (ARCHITECTURE §11).

    `parameters_json_schema` is a JSON-Schema object describing the tool input;
    it is generated from each tool's Pydantic `input_schema` by the registry.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$", min_length=1, max_length=64)
    description: str = Field(min_length=1)
    parameters_json_schema: dict[str, Any]

    def to_vendor_format(self) -> dict[str, Any]:
        """Return the OpenAI-compatible `{"type": "function", "function": ...}` payload."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_json_schema,
            },
        }


class ToolCall(BaseModel):
    """A tool invocation requested by the LLM.

    `arguments` MUST be a JSON object (dict); malformed JSON from the provider
    is a domain error, not a silently ignored item (ARCHITECTURE §11, SECURITY §13).
    """

    model_config = ConfigDict(frozen=True)

    id: str | None = None
    name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any]

    def to_vendor_format(self) -> dict[str, Any]:
        """OpenAI-compatible assistant-message tool_calls entry for the history."""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


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
    tools: list[ToolSpec] | None = None  # advertised tools (None → no tools field in payload)
    model: str | None = None  # None → provider default model from configuration
    temperature: float = Field(default=0.5, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    timeout: float | None = Field(default=None, ge=1.0)  # per-request override (seconds)
    extra_params: dict[str, Any] = Field(default_factory=dict)

    def compiled_messages(self) -> list[dict[str, Any]]:
        """Return the message list with the optional system prompt merged first."""
        compiled: list[dict[str, Any]] = []
        if self.system_prompt:
            compiled.append({"role": "system", "content": self.system_prompt})
        compiled.extend(message.to_compiled_dict() for message in self.messages)
        return compiled


class LLMResponse(BaseModel):
    """Normalized provider response returned to the application layer.

    `tool_calls` is not None only when the model requested tool invocations;
    a response may then carry empty `content` — for the agent loop this is a
    valid, non-empty outcome (ARCHITECTURE §11, iteration 5.1).
    """

    model_config = ConfigDict(frozen=True)

    content: str
    model: str
    provider: str
    finish_reason: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    request_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list[ToolCall] | None = None

    @field_validator("content")
    @classmethod
    def content_must_be_string(cls, value: str) -> str:
        """Enforce that content is a plain string (may be empty only if provider returned it)."""
        return value

    @property
    def has_tool_calls(self) -> bool:
        """True when the model requested at least one tool invocation."""
        return self.tool_calls is not None and len(self.tool_calls) > 0
