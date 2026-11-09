"""Unit tests for OpenAICompatibleProvider using httpx.MockTransport.

All tests are fully offline: no real cloud API and no local llama-server
are needed. Every behavioral branch of the adapter is covered:
normalization, empty response, timeout, connection error, rate limit,
authentication error, retries with backoff, and usage honesty.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from src.llm_providers.exceptions import (
    LLMAuthenticationError,
    LLMConnectionError,
    LLMEmptyResponseError,
    LLMInvalidRequestError,
    LLMProviderResponseError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from src.llm_providers.openai_like import OpenAICompatibleProvider
from src.llm_providers.schemas import LLMMessage, LLMRequest, TokenUsage

FAKE_API_KEY = "sk-test-not-a-real-secret-0001"
FAKE_BASE_URL = "http://llm.test/v1"

_KEY = FAKE_API_KEY


def _request(model: str | None = "test-model") -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content="Сколько стоит стрижка?")],
        system_prompt="Ты консультант салона.",
        temperature=0.3,
        model=model,
    )


def _openai_response(
    content: str = "Здравствуйте! Первичный подбор услуг я могу подсказать прямо сейчас.",
    *,
    include_usage: bool = True,
    model: str = "test-model",
    request_id: str = "chatcmpl-42",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": request_id,
        "object": "chat.completion",
        "created": 1700000000,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }
    if include_usage:
        body["usage"] = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    return body


def _make_provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_retries: int = 2,
    backoff_base: float = 0.0,
    default_model: str | None = "test-model",
) -> OpenAICompatibleProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return OpenAICompatibleProvider(
        base_url=FAKE_BASE_URL,
        api_key=_KEY,
        default_model=default_model,
        max_retries=max_retries,
        backoff_base=backoff_base,
        http=client,
    )


@pytest.mark.unit
@pytest.mark.asyncio
class TestOpenAICompatibleProvider:
    """Behavioral tests against a mocked OpenAI-compatible endpoint."""

    async def test_successful_request_and_normalization(self) -> None:
        """Happy path: correct payload, normalized response, honest usage."""
        captured: dict[str, Any] = {}

        def handler(sending: httpx.Request) -> httpx.Response:
            captured["url"] = str(sending.url)
            captured["auth"] = sending.headers.get("Authorization")
            captured["payload"] = json.loads(sending.content.decode("utf-8"))
            return httpx.Response(200, json=_openai_response())

        provider = _make_provider(handler)
        response = await provider.complete(_request())

        assert captured["url"] == f"{FAKE_BASE_URL}/chat/completions"
        assert captured["auth"] == f"Bearer {_KEY}"
        assert captured["payload"]["model"] == "test-model"
        assert captured["payload"]["temperature"] == 0.3
        assert captured["payload"]["messages"][0] == {
            "role": "system",
            "content": "Ты консультант салона.",
        }
        assert captured["payload"]["messages"][1]["content"] == "Сколько стоит стрижка?"
        assert "max_tokens" not in captured["payload"]

        assert response.content.startswith("Здравствуйте!")
        assert response.provider == "openai_compatible"
        assert response.model == "test-model"
        assert response.finish_reason == "stop"
        assert response.request_id == "chatcmpl-42"
        assert response.usage == TokenUsage(prompt_tokens=11, completion_tokens=7, total_tokens=18)
        assert response.usage.is_available

        await provider.aclose()

    async def test_missing_usage_absent_not_fabricated(self) -> None:
        """If the server does not return `usage`, totals must stay None."""

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_openai_response(include_usage=False))

        provider = _make_provider(handler)
        response = await provider.complete(_request())

        assert response.usage.is_available is False
        assert response.usage.prompt_tokens is None
        assert response.usage.total_tokens is None
        await provider.aclose()

    async def test_model_falls_back_to_provider_default(self) -> None:
        """request.model=None → default_model from configuration is used."""

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content.decode("utf-8"))
            assert payload["model"] == "test-model"
            return httpx.Response(200, json=_openai_response())

        provider = _make_provider(handler)
        await provider.complete(_request(model=None))
        await provider.aclose()

    async def test_default_model_missing_raises_configuration_error(self) -> None:
        from src.llm_providers.exceptions import LLMConfigurationError

        provider = _make_provider(
            lambda _: httpx.Response(200, json=_openai_response()), default_model=None
        )
        with pytest.raises(LLMConfigurationError):
            await provider.complete(_request(model=None))

    async def test_max_tokens_passthrough(self) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json=_openai_response())

        provider = _make_provider(handler)
        request = LLMRequest(
            messages=[LLMMessage(role="user", content="тест")],
            max_tokens=256,
            model="test-model",
        )
        await provider.complete(request)
        assert captured["payload"]["max_tokens"] == 256
        await provider.aclose()

    async def test_empty_response_rejected(self) -> None:
        """HTTP 200 with empty content → LLMEmptyResponseError, not retried."""
        calls = {"count": 0}

        def handler(_: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            return httpx.Response(200, json=_openai_response(content="   "))

        provider = _make_provider(handler, max_retries=3)
        with pytest.raises(LLMEmptyResponseError):
            await provider.complete(_request())
        assert calls["count"] == 1  # no retries for content problems
        await provider.aclose()

    async def test_no_choices_rejected(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"id": "1", "object": "chat.completion", "choices": []}
            )

        provider = _make_provider(handler)
        with pytest.raises(LLMEmptyResponseError):
            await provider.complete(_request())
        await provider.aclose()

    async def test_malformed_json_rejected(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>not json</html>")

        provider = _make_provider(handler)
        with pytest.raises(LLMProviderResponseError):
            await provider.complete(_request())
        await provider.aclose()

    async def test_authentication_error_not_retried(self) -> None:
        calls = {"count": 0}

        def handler(_: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            return httpx.Response(401, json={"error": {"message": "invalid api key"}})

        provider = _make_provider(handler, max_retries=5)
        with pytest.raises(LLMAuthenticationError) as info:
            await provider.complete(_request())
        assert info.value.status_code == 401
        assert info.value.retryable is False
        assert calls["count"] == 1
        await provider.aclose()

    async def test_invalid_request_error_not_retried(self) -> None:
        calls = {"count": 0}

        def handler(_: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            return httpx.Response(400, json={"error": {"message": "unknown parameter"}})

        provider = _make_provider(handler, max_retries=3)
        with pytest.raises(LLMInvalidRequestError) as info:
            await provider.complete(_request())
        assert info.value.status_code == 400
        assert calls["count"] == 1
        await provider.aclose()

    async def test_rate_limit_retries_with_backoff_then_success(self) -> None:
        state = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            state["count"] += 1
            if state["count"] < 3:
                return httpx.Response(
                    429,
                    json={"error": {"message": "rate limited"}},
                    headers={"Retry-After": "0.01"},
                )
            return httpx.Response(200, json=_openai_response())

        provider = _make_provider(handler, max_retries=3, backoff_base=0.01)
        response = await provider.complete(_request())
        assert state["count"] == 3
        assert response.content.startswith("Здравствуйте!")
        await provider.aclose()

    async def test_rate_limit_exhausted(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})

        provider = _make_provider(handler, max_retries=1, backoff_base=0.0)
        with pytest.raises(LLMRateLimitError) as info:
            await provider.complete(_request())
        assert info.value.retryable is True
        await provider.aclose()

    async def test_timeout_error(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out while reading response")

        provider = _make_provider(handler, max_retries=0)
        with pytest.raises(LLMTimeoutError) as info:
            await provider.complete(_request())
        assert info.value.retryable is True
        await provider.aclose()

    async def test_timeout_retries_then_raises(self) -> None:
        state = {"count": 0}

        def handler(_: httpx.Request) -> httpx.Response:
            state["count"] += 1
            raise httpx.ReadTimeout("timed out")

        provider = _make_provider(handler, max_retries=2, backoff_base=0.0)
        with pytest.raises(LLMTimeoutError):
            await provider.complete(_request())
        assert state["count"] == 3  # initial + 2 retries, no infinite loop
        await provider.aclose()

    async def test_connection_error(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        provider = _make_provider(handler, max_retries=0)
        with pytest.raises(LLMConnectionError) as info:
            await provider.complete(_request())
        assert info.value.retryable is True
        await provider.aclose()

    async def test_server_error_retries_then_raises(self) -> None:
        state = {"count": 0}

        def handler(_: httpx.Request) -> httpx.Response:
            state["count"] += 1
            return httpx.Response(503, json={"error": {"message": "upstream unavailable"}})

        provider = _make_provider(handler, max_retries=2, backoff_base=0.0)
        with pytest.raises(LLMProviderResponseError) as info:
            await provider.complete(_request())
        assert info.value.status_code == 503
        assert state["count"] == 3
        await provider.aclose()

    async def test_constructor_validation(self) -> None:
        from src.llm_providers.exceptions import LLMConfigurationError

        with pytest.raises(LLMConfigurationError):
            OpenAICompatibleProvider(base_url="  ", api_key=_KEY)
        with pytest.raises(LLMConfigurationError):
            OpenAICompatibleProvider(base_url=FAKE_BASE_URL, api_key="   ")
        with pytest.raises(LLMConfigurationError):
            OpenAICompatibleProvider(base_url=FAKE_BASE_URL, api_key=_KEY, timeout=0.0)
        with pytest.raises(LLMConfigurationError):
            OpenAICompatibleProvider(base_url=FAKE_BASE_URL, api_key=_KEY, max_retries=-1)
