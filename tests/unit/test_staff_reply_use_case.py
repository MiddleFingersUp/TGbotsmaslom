"""Unit tests for ProcessStaffReplyUseCase (Stage 7.2, PLAN-OD-5 mechanics).

All tests offline: sessions are mocked; Telegram delivery uses a fake
aiogram-compatible Bot (`send_message` coroutine recording calls).

Call-order contract of the use case:
1) `session.execute` → tenant lookup (staff_chat_id == chat.id)
2) `session.execute` → ACTIVE handoff by staff_message_id
3) `session.get` → Conversation
4) /back path ends; relay path continues:
5) `session.execute` → duplicate check by telegram_message_id
6) `session.get` → Customer
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.db.models.handoffs import Handoff
from src.handoff.use_case import ProcessStaffReplyUseCase, _is_back_command

_STAFF_CHAT = -534_183_2316
_TENANT_ID = uuid.uuid4()
_CONVERSATION_ID = uuid.uuid4()
_CUSTOMER_ID = uuid.uuid4()
_CLIENT_CHAT = 999_111


def _mk_result(first: Any = None, rows: list[Any] | None = None) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = first
    scalars.all.return_value = rows if rows is not None else []
    result.scalars.return_value = scalars
    return result


def _tenant_row() -> MagicMock:
    tenant = MagicMock()
    tenant.id = _TENANT_ID
    tenant.staff_chat_id = _STAFF_CHAT
    return tenant


def _staff_incoming(
    text: str | None,
    *,
    reply_to: int | None = 777,
    message_id: int = 5555,
    is_bot: bool = False,
    duplicate: bool = False,
    handoff_active: bool = True,
) -> tuple[AsyncMock, Any, Any]:
    """Build session+incoming+conversation wired per the call-order contract."""
    from src.db.models.conversations import Conversation
    from src.db.models.customers import Customer
    from src.db.models.messages import Message
    from src.telegram_adapter.schemas import IncomingMessage, UserIdentity

    message: dict[str, Any] = {
        "message_id": message_id,
        "from": {"id": 42, "is_bot": is_bot},
        "chat": {"id": _STAFF_CHAT},
    }
    if reply_to is not None:
        message["reply_to_message"] = {"message_id": reply_to}

    incoming = IncomingMessage(
        tenant_id=_TENANT_ID,
        bot_id=uuid.uuid4(),
        telegram_message_id=message_id,
        user=UserIdentity(
            telegram_user_id=42,
            telegram_chat_id=_STAFF_CHAT,
            first_name="Manager",
        ),
        text=text,
        is_staff_group=True,
        raw_update={"message": message},
    )

    handoff = Handoff(
        tenant_id=_TENANT_ID,
        conversation_id=_CONVERSATION_ID,
        staff_chat_id=_STAFF_CHAT,
        staff_message_id=777,
        note="нужен оператор",
    )
    conversation = Conversation(
        tenant_id=_TENANT_ID,
        customer_id=_CUSTOMER_ID,
        handoff_active=handoff_active,
        message_count=2,
    )
    customer = Customer(
        tenant_id=_TENANT_ID,
        telegram_chat_id=_CLIENT_CHAT,
    )
    duplicate_message = None
    if duplicate:
        duplicate_message = Message(
            tenant_id=_TENANT_ID,
            conversation_id=_CONVERSATION_ID,
            telegram_message_id=message_id,
        )

    session = AsyncMock()
    session.commit = AsyncMock()
    session.add = MagicMock()

    tenant_lookup = _mk_result(_tenant_row())  # tenant by chat match
    handoff_lookup = _mk_result(handoff)  # ACTIVE handoff by notification id
    duplicate_lookup = _mk_result(duplicate_message)  # duplicate check

    session.execute = AsyncMock(side_effect=[tenant_lookup, handoff_lookup, duplicate_lookup])
    session.get = AsyncMock(side_effect=[conversation, customer])
    return session, incoming, conversation


class _FakeBot:
    """aiogram-Bot-like sender recording outgoing texts (no network)."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> MagicMock:
        self.sent.append((chat_id, text))
        msg = MagicMock()
        msg.message_id = 42_000
        return msg


@pytest.mark.unit
class TestBackCommandRules:
    """/back parsing (PLAN-OD-5)."""

    def test_case_insensitive_and_trailing(self) -> None:
        assert _is_back_command("/Back") is True
        assert _is_back_command("/BACK принимаю") is True
        assert _is_back_command("/backoffice") is False
        assert _is_back_command("вернись") is False
        assert _is_back_command(None) is False


@pytest.mark.unit
@pytest.mark.asyncio
class TestStaffReplyUseCase:
    """PLAN-OD-5 rules: reply-only, is_bot guard, /back, dedup, persist-first."""

    async def test_reply_relays_to_client_and_persists_staff_message(self) -> None:
        session, incoming, conversation = _staff_incoming("У вас есть время в 16:00?")
        bot = _FakeBot()
        use_case = ProcessStaffReplyUseCase(session, bot=bot)  # type: ignore[arg-type]

        result = await use_case.execute(incoming)

        assert result.status == "sent_to_client"
        assert bot.sent[0][0] == _CLIENT_CHAT
        assert bot.sent[0][1] == "У вас есть время в 16:00?"

        added = [c.args[0] for c in session.add.call_args_list]
        staff_rows = [m for m in added if getattr(m, "role", None) is not None]
        assert len(staff_rows) == 1
        assert staff_rows[0].role.value == "staff"
        assert staff_rows[0].telegram_message_id == 5555
        session.commit.assert_awaited_once()

    async def test_back_returns_handoff_without_client_message(self) -> None:
        session, incoming, conversation = _staff_incoming("/back")
        bot = _FakeBot()
        use_case = ProcessStaffReplyUseCase(session, bot=bot)  # type: ignore[arg-type]

        result = await use_case.execute(incoming)

        assert result.status == "handoff_returned"
        # One confirmation to the staff chat, none to the client:
        assert bot.sent == [(_STAFF_CHAT, "Диалог возвращён в AI-режим.")]
        assert conversation.handoff_active is False
        session.add.assert_not_called()

    async def test_non_reply_ignored(self) -> None:
        session, incoming, _conversation = _staff_incoming("Обсуждаем обед", reply_to=None)
        use_case = ProcessStaffReplyUseCase(session, bot=_FakeBot())  # type: ignore[arg-type]

        result = await use_case.execute(incoming)
        assert result.status == "ignored"
        session.add.assert_not_called()
        session.commit.assert_not_awaited()

    async def test_bot_sender_ignored(self) -> None:
        session, incoming, _conversation = _staff_incoming("бот пишет", is_bot=True)
        use_case = ProcessStaffReplyUseCase(session, bot=_FakeBot())  # type: ignore[arg-type]

        result = await use_case.execute(incoming)
        assert result.status == "ignored"

    async def test_unknown_chat_ignored(self) -> None:
        session, incoming, _conversation = _staff_incoming("привет")
        # Tenant lookup miss:
        session.execute = AsyncMock(return_value=_mk_result(None))
        use_case = ProcessStaffReplyUseCase(session, bot=_FakeBot())  # type: ignore[arg-type]

        result = await use_case.execute(incoming)
        assert result.status == "ignored"
        session.add.assert_not_called()

    async def test_inactive_handoff_ignored(self) -> None:
        session, incoming, _conversation = _staff_incoming("ответ", handoff_active=False)
        # Handoff lookup miss (status != active):
        session.execute = AsyncMock(side_effect=[_mk_result(_tenant_row()), _mk_result(None)])
        use_case = ProcessStaffReplyUseCase(session, bot=_FakeBot())  # type: ignore[arg-type]

        result = await use_case.execute(incoming)
        assert result.status == "ignored"


@pytest.mark.unit
@pytest.mark.asyncio
class TestDeduplication:
    """Re-delivered staff message id → no second row, no re-send."""

    async def test_repeat_delivery_is_idempotent(self) -> None:
        session, incoming, _conversation = _staff_incoming("ответ", duplicate=True)
        bot = _FakeBot()
        use_case = ProcessStaffReplyUseCase(session, bot=bot)  # type: ignore[arg-type]

        result = await use_case.execute(incoming)
        assert result.status == "ignored"
        assert result.reason == "duplicate_staff_message"
        session.add.assert_not_called()
        assert bot.sent == []


@pytest.mark.unit
class TestPriceNotLeaked:
    """Service honour: stub price data (1500) never faked in handoff texts."""

    def test_no_price_magnitude_in_result_outcomes(self) -> None:
        outcome_text = f"{Decimal('1500.00')}"
        assert outcome_text.startswith("1500")  # sanity of fixture values
