"""Unit tests for ProcessIncomingMessageUseCase (message ingestion, echo, and deduplication)."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.process_incoming_message import ProcessIncomingMessageUseCase
from src.db.models.agents import Agent
from src.db.models.conversations import Conversation
from src.db.models.messages import Message
from src.telegram_adapter.schemas import IncomingMessage, UserIdentity


@pytest.mark.unit
@pytest.mark.asyncio
async def test_process_start_command_returns_agent_welcome() -> None:
    """Verify /start command returns the configured agent welcome_message."""
    tenant_id = uuid.uuid4()
    bot_id = uuid.uuid4()
    telegram_user_id = 12345
    chat_id = 12345

    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    # Mock execute results for:
    # 1) idempotency check -> None
    # 2) customer lookup -> None (will create)
    # 3) conversation lookup -> None (will create)
    # 4) agent lookup -> active Agent
    mock_msg_res = MagicMock()
    mock_msg_res.scalars.return_value.first.return_value = None

    mock_cust_res = MagicMock()
    mock_cust_res.scalars.return_value.first.return_value = None

    mock_conv_res = MagicMock()
    mock_conv_res.scalars.return_value.first.return_value = None

    mock_agent = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name="Sales Assistant",
        system_prompt="Rules",
        welcome_message="Добро пожаловать в салон 'Красота'!",
        is_active=True,
    )
    mock_agent_res = MagicMock()
    mock_agent_res.scalars.return_value.first.return_value = mock_agent

    mock_session.execute.side_effect = [
        mock_msg_res,
        mock_cust_res,
        mock_conv_res,
        mock_agent_res,
    ]

    use_case = ProcessIncomingMessageUseCase(mock_session)
    incoming = IncomingMessage(
        tenant_id=tenant_id,
        bot_id=bot_id,
        telegram_message_id=1,
        user=UserIdentity(
            telegram_user_id=telegram_user_id,
            telegram_chat_id=chat_id,
            first_name="Анна",
        ),
        text="/start",
    )

    result = await use_case.execute(incoming)

    assert result.is_duplicate is False
    assert result.response_text == "Добро пожаловать в салон 'Красота'!"
    assert mock_session.commit.assert_awaited_once


@pytest.mark.unit
@pytest.mark.asyncio
async def test_process_media_message_returns_polite_refusal() -> None:
    """Verify non-text media messages are rejected with FR-1.4 refusal message."""
    tenant_id = uuid.uuid4()
    bot_id = uuid.uuid4()

    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    mock_res = MagicMock()
    mock_res.scalars.return_value.first.return_value = None
    mock_session.execute.side_effect = [mock_res, mock_res, mock_res, mock_res]

    use_case = ProcessIncomingMessageUseCase(mock_session)
    incoming = IncomingMessage(
        tenant_id=tenant_id,
        bot_id=bot_id,
        telegram_message_id=2,
        user=UserIdentity(telegram_user_id=111, telegram_chat_id=111),
        is_media=True,
        media_type="photo",
    )

    result = await use_case.execute(incoming)

    assert "не умею обрабатывать медиафайлы" in result.response_text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_process_text_message_returns_echo_stub() -> None:
    """Verify regular user text message produces an echo response in Phase 3."""
    tenant_id = uuid.uuid4()
    bot_id = uuid.uuid4()

    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    mock_res = MagicMock()
    mock_res.scalars.return_value.first.return_value = None
    mock_session.execute.side_effect = [mock_res, mock_res, mock_res, mock_res]

    use_case = ProcessIncomingMessageUseCase(mock_session)
    incoming = IncomingMessage(
        tenant_id=tenant_id,
        bot_id=bot_id,
        telegram_message_id=3,
        user=UserIdentity(telegram_user_id=222, telegram_chat_id=222),
        text="Сколько стоит стрижка?",
    )

    result = await use_case.execute(incoming)

    assert "Вы написали: Сколько стоит стрижка?" in result.response_text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_idempotent_deduplication_of_messages() -> None:
    """Verify re-delivered Telegram update is recognized as duplicate without re-processing."""
    tenant_id = uuid.uuid4()
    bot_id = uuid.uuid4()
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

    mock_res = MagicMock()
    mock_res.scalars.return_value.first.return_value = existing_msg
    mock_session.execute.return_value = mock_res

    use_case = ProcessIncomingMessageUseCase(mock_session)
    incoming = IncomingMessage(
        tenant_id=tenant_id,
        bot_id=bot_id,
        telegram_message_id=100,
        user=UserIdentity(telegram_user_id=333, telegram_chat_id=333),
        text="Previous message re-delivered",
    )

    result = await use_case.execute(incoming)

    assert result.is_duplicate is True
    assert result.message_id == msg_id
    mock_session.commit.assert_not_called()
