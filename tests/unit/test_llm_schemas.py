"""Unit tests for normalized LLM request/response and token usage schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.llm_providers.schemas import LLMMessage, LLMRequest, LLMResponse, TokenUsage


@pytest.mark.unit
class TestLLMMessage:
    """Schema-level validation of conversation messages."""

    def test_valid_roles(self) -> None:
        message = LLMMessage(role="user", content="Сколько стоит стрижка?")
        assert message.role == "user"
        assert message.content == "Сколько стоит стрижка?"

    @pytest.mark.parametrize("role", ["admin", "human", ""])
    def test_invalid_role_rejected(self, role: str) -> None:
        with pytest.raises(ValidationError):
            LLMMessage(role=role, content="text")  # type: ignore[arg-type]

    def test_empty_content_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LLMMessage(role="user", content="")


@pytest.mark.unit
class TestLLMRequest:
    """Request input validation and system prompt compilation."""

    def _messages(self) -> list[LLMMessage]:
        return [LLMMessage(role="user", content="Привет")]

    def test_requires_at_least_one_message(self) -> None:
        with pytest.raises(ValidationError):
            LLMRequest(messages=[])

    def test_temperature_bounds(self) -> None:
        assert LLMRequest(messages=self._messages(), temperature=2.0).temperature == 2.0
        with pytest.raises(ValidationError):
            LLMRequest(messages=self._messages(), temperature=2.5)
        with pytest.raises(ValidationError):
            LLMRequest(messages=self._messages(), temperature=-0.1)

    def test_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            LLMRequest(messages=self._messages(), timeout=0.5)
        assert LLMRequest(messages=self._messages(), timeout=15.0).timeout == 15.0

    def test_compiled_messages_merges_system_prompt_first(self) -> None:
        request = LLMRequest(
            messages=[
                LLMMessage(role="user", content="Вопрос"),
                LLMMessage(role="assistant", content="Ответ"),
                LLMMessage(role="user", content="Уточнение"),
            ],
            system_prompt="Ты консультант салона.",
        )
        compiled = request.compiled_messages()
        assert compiled == [
            {"role": "system", "content": "Ты консультант салона."},
            {"role": "user", "content": "Вопрос"},
            {"role": "assistant", "content": "Ответ"},
            {"role": "user", "content": "Уточнение"},
        ]

    def test_compiled_messages_without_system_prompt(self) -> None:
        request = LLMRequest(messages=self._messages())
        assert request.compiled_messages() == [{"role": "user", "content": "Привет"}]


@pytest.mark.unit
class TestTokenUsage:
    """Usage honesty: absence is explicit, never fabricated."""

    def test_absent_usage(self) -> None:
        usage = TokenUsage()
        assert not usage.is_available
        assert usage.prompt_tokens is None
        assert usage.completion_tokens is None
        assert usage.total_tokens is None

    def test_partial_usage_counts_as_available(self) -> None:
        usage = TokenUsage(prompt_tokens=10)
        assert usage.is_available
        assert usage.completion_tokens is None

    def test_full_usage(self) -> None:
        usage = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        assert usage.is_available
        assert usage.total_tokens == 15

    def test_negative_counters_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TokenUsage(prompt_tokens=-1)


@pytest.mark.unit
class TestLLMResponse:
    """Normalized response contract."""

    def test_minimal_response(self) -> None:
        response = LLMResponse(content="Здравствуйте!", model="m", provider="mock")
        assert response.content == "Здравствуйте!"
        assert response.finish_reason is None
        assert response.request_id is None
        assert response.usage.is_available is False
        assert response.metadata == {}

    def test_full_response(self) -> None:
        response = LLMResponse(
            content="Ответ",
            model="gpt-test",
            provider="openai_compatible",
            finish_reason="stop",
            usage=TokenUsage(prompt_tokens=3, completion_tokens=4, total_tokens=7),
            request_id="chatcmpl-1",
            metadata={"created": 123},
        )
        assert response.usage.total_tokens == 7
        assert response.request_id == "chatcmpl-1"
