"""Unit tests for tool-call mapping in the OpenAI-compatible adapter.

Per docs/ARCHITECTURE.md §11 and DEVELOPMENT_PLAN Stage 5 iteration 5.1:
- advertised tools reach the vendor payload;
- `message.tool_calls` responses normalize into `ToolCall` objects and are a
  VALID non-empty outcome (no LLMEmptyResponseError);
- malformed arguments JSON is a domain error;
- only one request/response — the tool-call loop lives in Agent Core (5.2).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from src.llm_providers.exceptions import LLMEmptyResponseError, LLMInvalidRequestError
from src.llm_providers.openai_like import OpenAICompatibleProvider
from src.llm_providers.schemas import ToolSpec

_KEY = "sk-test-not-a-real-secret-0001"
_BASE_URL = "http://llm.test/v1"


def _request(with_tools: bool = False) -> Any:
    from src.llm_providers.schemas import LLMMessage, LLMRequest

    tools = (
        [
            ToolSpec(
                name="search_services",
                description="Поиск по каталогу услуг",
                parameters_json_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            )
        ]
        if with_tools
        else None
    )
    return LLMRequest(
        messages=[LLMMessage(role="user", content="Сколько стоит стрижка?")],
        system_prompt="Ты консультант.",
        tools=tools,
    )


def _tools_response(
    *, content: str | None = None, arguments: Any = '{"query": "стрижка"}'
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    message["tool_calls"] = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "search_services", "arguments": arguments},
        }
    ]
    return {
        "id": "chatcmpl-tools-1",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "test-model",
        "choices": [
            {"index": 0, "message": message, "finish_reason": "tool_calls"},
        ],
        "usage": {"prompt_tokens": 9, "completion_tokens": 5, "total_tokens": 14},
    }


def _plain_response(content: str = "Готовый ответ") -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12},
    }


def _make_provider(
    handler: Callable[[httpx.Request], httpx.Response],
    captured: dict[str, Any] | None = None,
) -> OpenAICompatibleProvider:
    def wrapped(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured["payload"] = json.loads(request.content.decode("utf-8"))
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    return OpenAICompatibleProvider(
        base_url=_BASE_URL,
        api_key=_KEY,
        default_model="test-model",
        http=client,
        max_retries=0,
    )


@pytest.mark.unit
class TestToolCallMapping:
    """Adapter behavior for tool-related request/response shapes."""

    async def test_tools_passed_to_payload(self) -> None:
        captured: dict[str, Any] = {}

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_plain_response())

        provider = _make_provider(handler, captured)
        request = _request(with_tools=True)
        await provider.complete(request)
        assert captured["payload"]["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "search_services",
                    "description": "Поиск по каталогу услуг",
                    "parameters": request.tools[0].parameters_json_schema,
                },
            }
        ]
        await provider.aclose()

    async def test_without_tools_no_tools_field_in_payload(self) -> None:
        captured: dict[str, Any] = {}

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_plain_response())

        provider = _make_provider(handler, captured)
        await provider.complete(_request(with_tools=False))
        assert "tools" not in captured["payload"]
        await provider.aclose()

    async def test_tool_call_response_normalized(self) -> None:
        """tool_calls + empty content = valid outcome; no LLMEmptyResponseError."""

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_tools_response())

        provider = _make_provider(handler)
        response = await provider.complete(_request())

        assert response.has_tool_calls is True
        assert response.tool_calls is not None
        call = response.tool_calls[0]
        assert call.id == "call_1"
        assert call.name == "search_services"
        assert call.arguments == {"query": "стрижка"}
        assert response.finish_reason == "tool_calls"
        assert response.usage.is_available is True
        await provider.aclose()

    async def test_tool_calls_with_content_keep_both(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_tools_response(content="Ищу услуги..."))

        provider = _make_provider(handler)
        response = await provider.complete(_request())
        assert response.content == "Ищу услуги..."
        assert response.tool_calls is not None
        await provider.aclose()

    async def test_arguments_as_dict_accepted(self) -> None:
        """Some servers return already-parsed argument objects."""

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_tools_response(arguments={"query": "маникюр"}))

        provider = _make_provider(handler)
        response = await provider.complete(_request())
        assert response.tool_calls is not None
        assert response.tool_calls[0].arguments == {"query": "маникюр"}
        await provider.aclose()

    async def test_arguments_missing_become_empty_object(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_tools_response(arguments=None))

        provider = _make_provider(handler)
        response = await provider.complete(_request())
        assert response.tool_calls is not None
        assert response.tool_calls[0].arguments == {}
        await provider.aclose()

    async def test_invalid_arguments_json_is_domain_error(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_tools_response(arguments="{not valid json"))

        provider = _make_provider(handler)
        with pytest.raises(LLMInvalidRequestError):
            await provider.complete(_request())
        await provider.aclose()

    async def test_arguments_scalar_is_domain_error(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_tools_response(arguments=42))

        provider = _make_provider(handler)
        with pytest.raises(LLMInvalidRequestError):
            await provider.complete(_request())
        await provider.aclose()

    async def test_missing_function_object_is_domain_error(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "c1"}]}}]
                },
            )

        provider = _make_provider(handler)
        with pytest.raises(LLMInvalidRequestError):
            await provider.complete(_request())
        await provider.aclose()

    async def test_missing_tool_name_is_domain_error(self) -> None:
        body = _tools_response(arguments='{"query": "x"}')
        body["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = ""  # type: ignore[index]

        def broken_handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=body)

        provider = _make_provider(broken_handler)
        with pytest.raises(LLMInvalidRequestError):
            await provider.complete(_request())
        await provider.aclose()

    async def test_empty_tool_calls_list_falls_back_to_content_rule(self) -> None:
        """Empty tool_calls list → ordinary path: empty content must still fail."""

        def handler(_: httpx.Request) -> httpx.Response:
            body = _plain_response(content="")
            body["choices"][0]["message"]["tool_calls"] = []  # type: ignore[index]
            return httpx.Response(200, json=body)

        provider = _make_provider(handler)
        with pytest.raises(LLMEmptyResponseError):
            await provider.complete(_request())
        await provider.aclose()

    async def test_no_tool_calls_leaves_field_none(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_plain_response())

        provider = _make_provider(handler)
        response = await provider.complete(_request())
        assert response.tool_calls is None
        assert response.has_tool_calls is False
        await provider.aclose()
