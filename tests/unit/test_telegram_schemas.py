"""Unit tests for normalized Telegram schemas."""

import uuid

import pytest

from src.telegram_adapter.schemas import IncomingMessage, OutgoingMessage, UserIdentity


@pytest.mark.unit
def test_incoming_message_normalization() -> None:
    """Verify IncomingMessage correctly models incoming update data."""
    tenant_id = uuid.uuid4()
    bot_id = uuid.uuid4()

    incoming = IncomingMessage(
        tenant_id=tenant_id,
        bot_id=bot_id,
        telegram_message_id=42,
        user=UserIdentity(
            telegram_user_id=111,
            telegram_chat_id=222,
            username="testuser",
            first_name="Ivan",
            language_code="ru",
        ),
        text="Hello agent",
    )

    assert incoming.tenant_id == tenant_id
    assert incoming.bot_id == bot_id
    assert incoming.telegram_message_id == 42
    assert incoming.user.telegram_user_id == 111
    assert incoming.text == "Hello agent"
    assert not incoming.is_media


@pytest.mark.unit
def test_outgoing_message_schema() -> None:
    """Verify OutgoingMessage response structure."""
    out = OutgoingMessage(chat_id=222, text="Response", reply_to_message_id=42)
    assert out.chat_id == 222
    assert out.text == "Response"
    assert out.reply_to_message_id == 42
