"""Deterministic in-memory provider for unit tests and offline development.

Makes no network calls, records every request it receives, and returns
scripted responses. Usage data is returned only if explicitly configured —
absence is signalled honestly, mirroring real-provider behavior.

Since Stage 5 §5.1 the scripted queue accepts both plain text responses and
`MockReply` objects with `tool_calls`, letting agent-loop tests simulate
multi-turn tool negotiation without any real API.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from src.llm_providers.base import LLMProvider
from src.llm_providers.exceptions import LLMProviderError
from src.llm_providers.schemas import LLMRequest, LLMResponse, TokenUsage, ToolCall


class MockReply(BaseModel):
    """One scripted provider outcome: text content and/or requested tool calls."""

    model_config = ConfigDict(frozen=True)

    content: str = ""
    tool_calls: list[ToolCall] | None = None

    @property
    def has_tool_calls(self) -> bool:
        """True when the reply requests at least one tool invocation."""
        return self.tool_calls is not None and len(self.tool_calls) > 0


ScriptedResponse = str | MockReply


class MockLLMProvider(LLMProvider):
    """Scriptable provider used in tests and local development."""

    provider_name: ClassVar[str] = "mock"

    def __init__(
        self,
        responses: list[ScriptedResponse] | None = None,
        *,
        usage: TokenUsage | None = None,
        fail_with: LLMProviderError | None = None,
        model_name: str = "mock-model",
    ) -> None:
        """Store the scripted queue (strings and/or MockReply objects) and cycle through it."""
        self._queue: list[MockReply] = []
        for item in responses or []:
            self._queue.append(MockReply(content=item) if isinstance(item, str) else item)
        self._usage = usage
        self._fail_with = fail_with
        self._model_name = model_name
        self._cursor = 0
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Return the next scripted response, or raise the configured error."""
        self.requests.append(request)

        if self._fail_with is not None:
            raise self._fail_with

        reply = self._next_reply(request)
        return LLMResponse(
            content=reply.content,
            model=self._model_name,
            provider=self.provider_name,
            finish_reason="tool_calls" if reply.has_tool_calls else "stop",
            usage=self._usage if self._usage is not None else TokenUsage(),
            request_id=None,
            metadata={"mock": True},
            tool_calls=reply.tool_calls,
        )

    def _next_reply(self, request: LLMRequest) -> MockReply:
        """Cycle scripted replies; fall back to a deterministic echo of the request."""
        if not self._queue:
            last_user = request.messages[-1].content
            return MockReply(content=f"[mock] {last_user}")
        reply = self._queue[self._cursor % len(self._queue)]
        self._cursor += 1
        return reply
