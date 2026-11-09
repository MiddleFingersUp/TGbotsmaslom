"""Unit tests for the offline MockLLMProvider."""

from __future__ import annotations

import pytest

from src.llm_providers.exceptions import LLMRateLimitError
from src.llm_providers.mock import MockLLMProvider
from src.llm_providers.schemas import LLMMessage, LLMRequest, LLMResponse, TokenUsage


def _request(text: str = "Чем вы можете помочь?", with_system: bool = True) -> LLMRequest:
    messages = [LLMMessage(role="user", content=text)]
    return LLMRequest(
        messages=messages,
        system_prompt="Ты консультант." if with_system else None,
    )


@pytest.mark.unit
@pytest.mark.asyncio
class TestMockLLMProvider:
    """Deterministic behavior without any network calls."""

    async def test_default_echo_response(self) -> None:
        provider = MockLLMProvider()
        response = await provider.complete(_request())

        assert isinstance(response, LLMResponse)
        assert response.provider == "mock"
        assert response.content == "[mock] Чем вы можете помочь?"
        assert response.finish_reason == "stop"

    async def test_usage_absent_by_default(self) -> None:
        """Mock must not fabricate token usage unless explicitly configured."""
        provider = MockLLMProvider()
        response = await provider.complete(_request())
        assert response.usage.is_available is False

    async def test_configured_usage_returned(self) -> None:
        usage = TokenUsage(prompt_tokens=5, completion_tokens=3, total_tokens=8)
        provider = MockLLMProvider(usage=usage)
        response = await provider.complete(_request())
        assert response.usage == usage

    async def test_scripted_responses_cycle(self) -> None:
        provider = MockLLMProvider(responses=["Первый", "Второй"])
        first = await provider.complete(_request())
        second = await provider.complete(_request())
        third = await provider.complete(_request())

        assert first.content == "Первый"
        assert second.content == "Второй"
        assert third.content == "Первый"  # cycles

    async def test_configured_error_raised(self) -> None:
        error = LLMRateLimitError("limit", provider="mock", retry_after=0.0)
        provider = MockLLMProvider(fail_with=error)
        with pytest.raises(LLMRateLimitError):
            await provider.complete(_request())

    async def test_requests_recorded_with_system_prompt_merged(self) -> None:
        provider = MockLLMProvider()
        source = _request()
        await provider.complete(source)

        assert len(provider.requests) == 1
        recorded = provider.requests[0]
        assert recorded is source
        compiled = recorded.compiled_messages() if isinstance(recorded, LLMRequest) else []
        assert compiled[0]["role"] == "system"

    async def test_is_a_provider_instance(self) -> None:
        from src.llm_providers.base import LLMProvider

        provider = MockLLMProvider()
        assert isinstance(provider, LLMProvider)
