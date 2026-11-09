"""Unit tests for ProcessIncomingMessageUseCase: spec cases, agent path, dedup."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent_core.agent_loop import AgentLoop
from src.application.process_incoming_message import ProcessIncomingMessageUseCase
from src.db.models.agents import Agent
from src.db.models.conversations import Conversation
from src.db.models.enums import ConversationState, MessageRole
from src.db.models.messages import Message
from src.llm_providers.mock import MockLLMProvider
from src.telegram_adapter.schemas import IncomingMessage, UserIdentity
from src.tools.stubs import build_default_stub_registry


def _mock_result(value: Any = None) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = value
    scalars.all.return_value = value if isinstance(value, list) else []
    result.scalars.return_value = scalars
    return result


def _agent(tenant_id: uuid.UUID, welcome: str = "Добро пожаловать в салон 'Красота'!") -> Agent:
    return Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name="Sales Assistant",
        system_prompt="Правила компании.",
        welcome_message=welcome,
        max_context_messages=12,
        temperature=0.5,
        allowed_tools=["search_services", "search_faq", "save_lead", "handoff_request"],
        is_active=True,
    )


def _incoming(
    tenant_id: uuid.UUID, *, text: str | None = None, is_media: bool = False
) -> IncomingMessage:
    return IncomingMessage(
        tenant_id=tenant_id,
        bot_id=uuid.uuid4(),
        telegram_message_id=int.from_bytes(uuid.uuid4().bytes[:4], "little"),
        user=UserIdentity(
            telegram_user_id=int.from_bytes(uuid.uuid4().bytes[:4], "little") % 10**9,
            telegram_chat_id=int.from_bytes(uuid.uuid4().bytes[:4], "little") % 10**9,
            first_name="Анна",
        ),
        text=text,
        is_media=is_media,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_process_start_command_returns_agent_welcome() -> None:
    """Verify /start command returns the configured agent welcome_message."""
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.execute.side_effect = [
        _mock_result(None),  # idempotency
        _mock_result(None),  # customer lookup
        _mock_result(None),  # conversation lookup
        _mock_result(agent),  # agent lookup
    ]

    loop = AgentLoop(MockLLMProvider(), build_default_stub_registry())
    use_case = ProcessIncomingMessageUseCase(mock_session, agent_loop=loop)
    incoming = _incoming(tenant_id, text="/start")

    result = await use_case.execute(incoming)

    assert result.is_duplicate is False
    assert result.response_text == "Добро пожаловать в салон 'Красота'!"
    mock_session.commit.assert_awaited_once()
    # /start never reaches the LLM (welcome message is a backend constant)
    assert loop.provider.requests == [] if isinstance(loop.provider, MockLLMProvider) else True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_process_media_message_returns_polite_refusal() -> None:
    """Verify non-text media messages are rejected with FR-1.4 refusal message."""
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id)

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.execute.side_effect = [
        _mock_result(None),
        _mock_result(None),
        _mock_result(None),
        _mock_result(agent),
    ]

    loop = AgentLoop(MockLLMProvider(), build_default_stub_registry())
    use_case = ProcessIncomingMessageUseCase(mock_session, agent_loop=loop)
    incoming = _incoming(tenant_id, is_media=True)

    result = await use_case.execute(incoming)

    assert "не умею обрабатывать медиафайлы" in result.response_text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_process_text_message_goes_through_agent_path() -> None:
    """Plain text produces an LLM reply (echo stub replaced in Stage 5.2)."""
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id, welcome="Welcome")

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.execute.side_effect = [
        _mock_result(None),
        _mock_result(None),
        _mock_result(None),
        _mock_result(agent),
        _mock_result([]),  # conversation history for the loop
    ]

    provider = MockLLMProvider(responses=["Здравствуйте! Стрижка есть в каталоге."])
    loop = AgentLoop(provider, build_default_stub_registry())
    use_case = ProcessIncomingMessageUseCase(mock_session, agent_loop=loop)

    result = await use_case.execute(_incoming(tenant_id, text="Сколько стоит стрижка?"))

    assert result.response_text == "Здравствуйте! Стрижка есть в каталоге."
    assert provider.requests != []  # LLM was actually consulted

    added = [call.args[0] for call in mock_session.add.call_args_list]
    agent_messages = [
        obj for obj in added if isinstance(obj, Message) and obj.role == MessageRole.AGENT
    ]
    assert len(agent_messages) == 1
    assert agent_messages[0].ai_model == "mock-model"
    assert agent_messages[0].meta is not None
    assert agent_messages[0].meta["fallback"] is False

    conversations = [obj for obj in added if isinstance(obj, Conversation)]
    # New conversation advanced: greeting -> need_discovery after first reply
    existing = conversations + [obj for obj in added if isinstance(obj, Conversation)]
    states = {conv.state for conv in existing}
    assert ConversationState.NEED_DISCOVERY in states or ConversationState.GREETING in states


@pytest.mark.unit
@pytest.mark.asyncio
async def test_process_text_message_in_handoff_mode_skips_llm() -> None:
    """handoff_active=True → no LLM call, empty response, user message kept."""
    tenant_id = uuid.uuid4()
    agent = _agent(tenant_id, welcome="Welcome")
    customer_id = uuid.uuid4()
    conversation = Conversation(
        tenant_id=tenant_id,
        customer_id=customer_id,
        state=ConversationState.CONSULTATION,
        handoff_active=True,
        message_count=0,
    )

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.execute.side_effect = [
        _mock_result(None),
        _mock_result(None),
        _mock_result(conversation),
        _mock_result(agent),
        _mock_result([]),
    ]

    provider = MockLLMProvider(responses=["НЕЛЬЗЯ ВЫЗЫВАТЬ"])
    loop = AgentLoop(provider, build_default_stub_registry())
    use_case = ProcessIncomingMessageUseCase(mock_session, agent_loop=loop)

    result = await use_case.execute(_incoming(tenant_id, text="Алло, вы там?"))

    assert result.response_text == ""
    assert provider.requests == []  # ARCHITECTURE §13: zero LLM calls in handoff


@pytest.mark.unit
@pytest.mark.asyncio
async def test_idempotent_deduplication_of_messages() -> None:
    """Verify re-delivered Telegram update is recognized as duplicate."""
    tenant_id = uuid.uuid4()
    customer_id = uuid.uuid4()
    conv_id = uuid.uuid4()
    msg_id = uuid.uuid4()

    mock_session = AsyncMock()

    existing_conv = Conversation(id=conv_id, tenant_id=tenant_id, customer_id=customer_id)
    existing_msg = Message(
        id=msg_id,
        tenant_id=tenant_id,
        conversation_id=conv_id,
        telegram_message_id=100,
        text="Previous message",
    )
    existing_msg.conversation = existing_conv

    mock_session.execute.side_effect = [_mock_result(existing_msg)]

    use_case = ProcessIncomingMessageUseCase(
        mock_session, agent_loop=AgentLoop(MockLLMProvider(), build_default_stub_registry())
    )
    incoming = IncomingMessage(  # re-delivered id 100
        tenant_id=tenant_id,
        bot_id=uuid.uuid4(),
        telegram_message_id=100,
        user=UserIdentity(telegram_user_id=333, telegram_chat_id=333),
        text="Re-delivered",
    )

    result = await use_case.execute(incoming)

    assert result.is_duplicate is True
    assert result.message_id == msg_id
    mock_session.commit.assert_not_awaited()
