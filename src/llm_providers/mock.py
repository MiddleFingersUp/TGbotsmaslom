"""Deterministic in-memory provider for unit tests and offline development.

Makes no network calls, records every request it receives, and returns
scripted responses. Usage data is returned only if explicitly configured —
absence is signalled honestly, mirroring real-provider behavior.
"""

from __future__ import annotations

from typing import ClassVar

from src.llm_providers.base import LLMProvider
from src.llm_providers.exceptions import LLMProviderError
from src.llm_providers.schemas import LLMRequest, LLMResponse, TokenUsage


class MockLLMProvider(LLMProvider):
    """Scriptable provider used in tests and local development."""

    provider_name: ClassVar[str] = "mock"

    def __init__(
        self,
        responses: list[str] | None = None,
        *,
        usage: TokenUsage | None = None,
        fail_with: LLMProviderError | None = None,
        model_name: str = "mock-model",
    ) -> None:
        self._responses: list[str] = list(responses or [])
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

        content = self._next_response(request)
        return LLMResponse(
            content=content,
            model=self._model_name,
            provider=self.provider_name,
            finish_reason="stop",
            usage=self._usage if self._usage is not None else TokenUsage(),
            request_id=None,
            metadata={"mock": True},
        )

    def _next_response(self, request: LLMRequest) -> str:
        """Cycle scripted responses; fall back to echoing the last user message."""
        if not self._responses:
            last_user = request.messages[-1].content
            return f"[mock] {last_user}"
        content = self._responses[self._cursor % len(self._responses)]
        self._cursor += 1
        return content
