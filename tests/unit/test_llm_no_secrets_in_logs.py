"""Security tests: no API keys, authorization headers or request bodies in logs.

Per docs/SECURITY.md §3 and §8: the provider layer must never log secrets,
headers or full request/response content. These tests exercise the real
logging path (structlog + stdlib capture) to verify the guarantee.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from src.llm_providers.openai_like import OpenAICompatibleProvider
from src.llm_providers.schemas import LLMMessage, LLMRequest
from src.monitoring.logging import setup_logging

pytestmark = [pytest.mark.security, pytest.mark.unit]

_API_KEY = "sk-very-secret-test-key-do-not-log-777"
_BASE_URL = "http://llm.test/v1"


def _request() -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content="Привет, сколько стоит?")],
        system_prompt="Ты консультант.",
        model="test-model",
    )


def _retry_response_factory() -> Any:
    """Handler returning a 5xx error whose body mentions the key (to prove it is not logged)."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"error": {"message": f"internal error near {_API_KEY}"}},
        )

    return handler


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.asyncio
async def test_no_secrets_in_logs_on_provider_error(caplog: pytest.LogCaptureFixture) -> None:
    """API key never appears in any emitted log record, even in error paths."""
    setup_logging(log_level="DEBUG", json_logs=True)
    transport = httpx.MockTransport(_retry_response_factory())
    client = httpx.AsyncClient(transport=transport)
    provider = OpenAICompatibleProvider(
        base_url=_BASE_URL,
        api_key=_API_KEY,
        max_retries=1,
        backoff_base=0.0,
        http=client,
    )

    from src.llm_providers.exceptions import LLMProviderResponseError

    with pytest.raises(LLMProviderResponseError), caplog.at_level(logging.DEBUG):
        await provider.complete(_request())

    logs_text = caplog.text
    assert _API_KEY not in logs_text
    assert "Bearer" not in logs_text
    assert "Привет, сколько стоит?" not in logs_text  # request body content not logged

    await provider.aclose()


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.asyncio
async def test_no_secrets_in_logs_on_successful_call(caplog: pytest.LogCaptureFixture) -> None:
    """Even the success path logs only selectors (model, status), never the key."""
    setup_logging(log_level="DEBUG", json_logs=True)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-x",
                "object": "chat.completion",
                "created": 1,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Ответ"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        )

    provider = OpenAICompatibleProvider(
        base_url=_BASE_URL,
        api_key=_API_KEY,
        max_retries=0,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    response = await provider.complete(_request())
    assert response.content == "Ответ"

    logs_text = caplog.text
    assert _API_KEY not in logs_text
    assert "sk-" not in logs_text

    # What we know IS logged: the payload sent over the wire contains the key
    # header but the log messages must not contain the JSON request body.
    for _record in caplog.records:
        assert not any(
            isinstance(arg, dict) and "messages" in arg
            for arg in _record.args
            if isinstance(arg, dict)
        )

    await provider.aclose()


@pytest.mark.unit
@pytest.mark.security
def test_structlog_censor_redacts_secret_keys() -> None:
    """Invariant used by the provider logger: sensitive keys are redacted."""
    from src.monitoring.logging import censor_secrets

    event: dict[str, Any] = {"model": "gpt-test", "api_key": _API_KEY, "content": "тест"}
    censored = censor_secrets(None, "debug", event)
    assert censored["api_key"] == "[REDACTED]"
    assert censored["model"] == "gpt-test"
    assert _API_KEY not in json.dumps(censored)
