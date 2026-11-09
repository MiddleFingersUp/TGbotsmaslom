"""Unit tests for MockLLMProvider scenario queue with tool-call replies."""

from __future__ import annotations

import pytest

from src.llm_providers.mock import MockLLMProvider, MockReply
from src.llm_providers.schemas import LLMMessage, LLMRequest, ToolCall


def _request(text: str = "Сколько стоит стрижка?") -> LLMRequest:
    return LLMRequest(messages=[LLMMessage(role="user", content=text)])


@pytest.mark.unit
class TestMockScenarioQueue:
    """Scripted replies (text and/or tool calls) are returned in order."""

    async def test_tool_call_reply(self) -> None:
        reply = MockReply(
            tool_calls=[ToolCall(id="c1", name="search_services", arguments={"query": "стрижка"})]
        )
        provider = MockLLMProvider([reply])

        response = await provider.complete(_request())

        assert response.has_tool_calls is True
        assert response.tool_calls is not None
        assert response.tool_calls[0].name == "search_services"
        assert response.tool_calls[0].arguments == {"query": "стрижка"}
        assert response.finish_reason == "tool_calls"
        assert response.metadata == {"mock": True}

    async def test_text_and_tool_call_scenarios_in_order(self) -> None:
        provider = MockLLMProvider(
            [
                "Привет! Подбираем услуги...",
                MockReply(
                    tool_calls=[
                        ToolCall(id="c1", name="search_faq", arguments={"query": "график"})
                    ]
                ),
                "Вопрос закрыт.",
            ]
        )
        first = await provider.complete(_request())
        second = await provider.complete(_request())
        third = await provider.complete(_request())

        # 1: plain text stop
        assert first.content == "Привет! Подбираем услуги..."
        assert first.has_tool_calls is False
        assert first.finish_reason == "stop"
        # 2: tool call request
        assert second.tool_calls is not None
        assert second.finish_reason == "tool_calls"
        # 3: final content after the tool executed
        assert third.content == "Вопрос закрыт."
        assert third.tool_calls is None

    async def test_queue_cycles(self) -> None:
        reply = MockReply(
            tool_calls=[ToolCall(id="c1", name="save_lead", arguments={"intent": "запись"})]
        )
        provider = MockLLMProvider([reply])
        first = await provider.complete(_request())
        second = await provider.complete(_request())

        assert first.has_tool_calls and second.has_tool_calls
        assert first.tool_calls == second.tool_calls

    async def test_empty_queue_falls_back_to_echo(self) -> None:
        provider = MockLLMProvider()
        response = await provider.complete(_request())
        assert response.content == "[mock] Сколько стоит стрижка?"
        assert response.has_tool_calls is False

    async def test_mock_reply_has_tool_calls_property(self) -> None:
        assert MockReply(tool_calls=None).has_tool_calls is False
        assert MockReply().has_tool_calls is False
        with_tool = MockReply(tool_calls=[ToolCall(id=None, name="x", arguments={})])
        assert with_tool.has_tool_calls is True
