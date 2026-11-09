"""Polite client-facing fallback texts for degraded agent turns.

Per docs/ARCHITECTURE.md §17 and docs/SECURITY.md: fallbacks replace any
failed LLM/tool turn with a defined Russian phrase — the client never sees
stack traces, provider names, or empty answers.
"""

from __future__ import annotations

from src.llm_providers.exceptions import (
    LLMAuthenticationError,
    LLMConnectionError,
    LLMEmptyResponseError,
    LLMProviderResponseError,
    LLMRateLimitError,
    LLMTimeoutError,
)

FALLBACK_LLM_UNAVAILABLE = (
    "Извините, у меня сейчас техническая пауза, и я не смог подготовить ответ. "
    "Пожалуйста, попробуйте ещё раз чуть позже."
)

FALLBACK_TOOL_OVERFLOW = (
    "Я начал обрабатывать ваш вопрос, но разговор получается сложным. "
    "Пожалуйста, напишите самый важный запрос ещё раз коротко."
)

FALLBACK_RESPONSE_INVALID = (
    "Извините, произошла ошибка при подготовке ответа. Пожалуйста, повторите ваш вопрос позже."
)


def fallback_for(error: Exception) -> str:
    """Pick the fallback phrase matching a provider failure."""
    if isinstance(error, (LLMTimeoutError, LLMConnectionError, LLMRateLimitError)):
        return FALLBACK_LLM_UNAVAILABLE
    if isinstance(
        error, (LLMProviderResponseError, LLMEmptyResponseError, LLMAuthenticationError)
    ):
        return FALLBACK_LLM_UNAVAILABLE
    return FALLBACK_LLM_UNAVAILABLE
