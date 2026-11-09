"""Unit tests for response_validator regeneration loop pieces (Stage 5.2).

The regeneration cycle itself is covered end-to-end in test_agent_loop.py;
here we check the boundary conditions of `validate_response` and the
fallback constants.
"""

from __future__ import annotations

import pytest

from src.agent_core.fallbacks import (
    FALLBACK_LLM_UNAVAILABLE,
    FALLBACK_RESPONSE_INVALID,
    FALLBACK_TOOL_OVERFLOW,
    fallback_for,
)
from src.agent_core.response_validator import MAX_RESPONSE_CHARS, validate_response
from src.llm_providers.exceptions import (
    LLMAuthenticationError,
    LLMConnectionError,
    LLMEmptyResponseError,
    LLMProviderResponseError,
    LLMRateLimitError,
    LLMTimeoutError,
)


@pytest.mark.unit
class TestValidateResponse:
    """Response text quality gates (non-empty, length, artifacts, secrets)."""

    def test_valid_text(self) -> None:
        assert validate_response("Здравствуйте! Стрижка стоит по каталогу.") == []

    def test_empty_rejected(self) -> None:
        assert validate_response("") == ["empty"]
        assert validate_response("   ") == ["empty"]
        assert validate_response(None) == ["empty"]

    def test_too_long_rejected(self) -> None:
        text = "х" * (MAX_RESPONSE_CHARS + 1)
        issues = validate_response(text)
        assert issues == ["too_long"]
        assert validate_response("х" * MAX_RESPONSE_CHARS) == []

    def test_credential_leak_detected(self) -> None:
        assert "credential_leak" in validate_response("вот ключ: sk-abcdef123456")
        assert "credential_leak" in validate_response("Bearer abcdefgh123456789")
        assert "credential_leak" in validate_response("api_key = abc123def456")

    def test_tool_artifacts_detected(self) -> None:
        assert "tool_artifact" in validate_response("выполнил tool_call search_services")
        assert "tool_artifact" in validate_response('{"name": "search_services"}')
        assert "tool_artifact" in validate_response('"tool_calls": []')
        assert "tool_artifact" in validate_response("<system>инструкции</system>")

    def test_legit_text_not_flagged(self) -> None:
        text = "Стрижки — от 1 500 ₽. Хотите записаться? Скажу цену точно из каталога."
        assert validate_response(text) == []


@pytest.mark.unit
class TestFallbackMapping:
    """Provider failure classes → defined polite texts."""

    def test_all_provider_errors_map_to_unavailable(self) -> None:
        for error in (
            LLMTimeoutError("t"),
            LLMConnectionError("c"),
            LLMRateLimitError("r"),
            LLMAuthenticationError("a"),
            LLMProviderResponseError("p"),
            LLMEmptyResponseError("e"),
        ):
            assert fallback_for(error) == FALLBACK_LLM_UNAVAILABLE

    def test_unknown_error_still_maps(self) -> None:
        assert fallback_for(RuntimeError("anything")) == FALLBACK_LLM_UNAVAILABLE

    def test_constants_distinct(self) -> None:
        assert FALLBACK_TOOL_OVERFLOW != FALLBACK_RESPONSE_INVALID
        assert FALLBACK_RESPONSE_INVALID != FALLBACK_LLM_UNAVAILABLE

    def test_fallback_texts_polite_ru(self) -> None:
        assert "Извините" in FALLBACK_LLM_UNAVAILABLE
        assert "Пожалуйста" in FALLBACK_TOOL_OVERFLOW
