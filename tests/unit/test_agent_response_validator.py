"""Unit tests for response_validator outcomes inside the agent loop.

Covers: invalid first answer → one regeneration without tools → success;
invalid twice → fallback. Uses scripted MockLLMProvider replies.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent_core.agent_loop import AgentLoop
from src.agent_core.fallbacks import FALLBACK_RESPONSE_INVALID
from src.db.context import TenantContext
from src.db.models.agents import Agent
from src.db.models.conversations import Conversation
from src.db.models.enums import ConversationState
from src.llm_providers.mock import MockLLMProvider


def _tenant() -> TenantContext:
    return TenantContext(tenant_id=uuid.uuid4())


def _agent() -> Agent:
    return Agent(
        tenant_id=uuid.uuid4(),
        name="Sales Assistant",
        system_prompt="Ты консультант.",
        welcome_message="Hi",
        language="ru",
        max_context_messages=12,
        temperature=0.5,
        allowed_tools=["search_services"],
        is_active=True,
    )


def _conversation() -> Conversation:
    return Conversation(
        tenant_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        state=ConversationState.NEED_DISCOVERY,
        handoff_active=False,
        message_count=2,
    )


def _mock_session() -> AsyncMock:
    session = AsyncMock()
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = []
    scalars.first.return_value = None
    result.scalars.return_value = scalars
    session.execute.return_value = result
    return session


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tool_artifact_answer_regenerated_without_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First answer contains a tool artifact → regenerate → clean reply wins."""
    provider = MockLLMProvider(
        responses=[
            "Конечно! выполненный tool_call search_services завершён.",  # artifact → invalid
            "Стрижки есть, цена по каталогу.",  # valid
        ]
    )
    loop = AgentLoop(provider, build_registry())
    result = await loop.run(_mock_session(), _tenant(), _agent(), _conversation())

    assert result.response_text == "Стрижки есть, цена по каталогу."
    assert result.used_fallback is False
    assert result.llm_rounds == 2
    # The regeneration round must NOT advertise tools (lock after invalidation).
    assert provider.requests[1].tools is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalid_twice_falls_back() -> None:
    """Answer invalid after regeneration → defined fallback, never raw text."""
    bad_tool_answer = "x" * 2500  # too long
    provider = MockLLMProvider(responses=[bad_tool_answer, bad_tool_answer])
    loop = AgentLoop(provider, build_registry())
    result = await loop.run(_mock_session(), _tenant(), _agent(), _conversation())

    assert result.used_fallback is True
    assert result.response_text == FALLBACK_RESPONSE_INVALID


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_reply_regenerated_successfully() -> None:
    """Empty content regenerates; validated clean reply accepted."""
    provider = MockLLMProvider(responses=["   ", "Извините, уточню: какие услуги интересуют?"])
    loop = AgentLoop(provider, build_registry())
    result = await loop.run(_mock_session(), _tenant(), _agent(), _conversation())

    assert result.response_text == "Извините, уточню: какие услуги интересуют?"
    assert result.used_fallback is False


def build_registry() -> Any:
    """Stub registry limited to search (validator-focused tests)."""
    from src.tools.registry import ToolRegistry
    from src.tools.stubs import SearchServicesTool

    registry = ToolRegistry()
    registry.register(SearchServicesTool())
    return registry
