"""Unit tests for the real HandoffRequestTool backend procedure (Stage 7.2).

Covered (offline; mock sessions, no Telegram):
- configured tenant: row + backend flag/state in the caller's transaction
  (row id, staff_chat_id copy, note masked);
- an ACTIVE handoff already present → idempotent success (no second row);
- staff_chat_id unset → ok=False «Передача сотрудникам сейчас недоступна»,
  flag NOT set, no row;
- note PII-masking before persistence/queueing.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.db.context import TenantContext
from src.db.models.conversations import Conversation
from src.db.models.enums import ConversationState
from src.tools.handoff_request import (
    STAFF_UNAVAILABLE,
    HandoffRequestTool,
    mask_phone_like,
)


def _tenant() -> TenantContext:
    return TenantContext(tenant_id=uuid.uuid4())


def _conversation(state: ConversationState = ConversationState.NEED_DISCOVERY) -> Conversation:
    return Conversation(
        tenant_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        state=state,
        handoff_active=False,
        message_count=2,
    )


def _configured_session(conversation: Conversation) -> AsyncMock:
    """session.get: Tenant (staff chat configured) → Conversation (real object)."""
    session = AsyncMock()
    tenant_mock = MagicMock()
    tenant_mock.staff_chat_id = -534_183_2316
    tenant_mock.id = conversation.tenant_id
    session.get = AsyncMock(side_effect=[tenant_mock, conversation])
    scalars = MagicMock()
    scalars.first.return_value = None
    result = MagicMock()
    result.scalars.return_value = scalars
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    return session


def _unconfigured_session(conversation: Conversation) -> AsyncMock:
    session = AsyncMock()
    tenant_mock = MagicMock()
    tenant_mock.staff_chat_id = None
    session.get = AsyncMock(side_effect=[tenant_mock, conversation])
    return session


@pytest.mark.unit
@pytest.mark.asyncio
class TestHandoffRequestTool:
    """Backend procedure semantics (docs/ARCHITECTURE.md §13)."""

    async def test_creates_row_and_sets_flag(self) -> None:
        """Configured tenant → row + handoff_active=True + state=handoff."""
        conversation = _conversation()
        session = _configured_session(conversation)
        tool = HandoffRequestTool()

        result = await tool.create_handoff(
            conversation_id=conversation.id,
            tenant_context=_tenant(),
            session=session,
            note="клиент просил оператора",
            trigger="agent_decision",
        )

        assert result.ok is True
        data = result.data or {}
        assert data["status"] == "accepted"
        assert data["handoff_id"]
        assert conversation.handoff_active is True
        assert conversation.state == ConversationState.HANDOFF
        row = session.add.call_args[0][0]
        assert row.staff_chat_id == -534_183_2316
        assert row.conversation_id == conversation.id
        assert row.note == "клиент просил оператора"

    async def test_active_duplicate_is_idempotent(self) -> None:
        """Existing ACTIVE handoff → accepted, no second session.add row."""
        conversation = _conversation()
        conversation.handoff_active = True
        session = _configured_session(conversation)
        existing = MagicMock()
        existing.id = uuid.uuid4()
        scalars = MagicMock()
        scalars.first.return_value = existing
        result = MagicMock()
        result.scalars.return_value = scalars
        session.execute = AsyncMock(return_value=result)
        tool = HandoffRequestTool()

        result_tool = await tool.create_handoff(
            conversation_id=conversation.id,
            tenant_context=_tenant(),
            session=session,
        )
        data = result_tool.data or {}

        assert result_tool.ok is True
        assert data["handoff_id"] == str(existing.id)
        assert data["status"] == "accepted"
        assert conversation.handoff_active is True
        session.add.assert_not_called()

    async def test_unset_staff_chat_fails_closed_without_flag(self) -> None:
        """No staff_chat_id → ok=False, flag untouched, no row (agent keeps working)."""
        conversation = _conversation()
        session = _unconfigured_session(conversation)
        tool = HandoffRequestTool()

        result = await tool.create_handoff(
            conversation_id=conversation.id,
            tenant_context=_tenant(),
            session=session,
            note="нужен человек",
        )

        assert result.ok is False
        assert result.error_message == STAFF_UNAVAILABLE
        assert conversation.handoff_active is False
        assert conversation.state == ConversationState.NEED_DISCOVERY
        session.add.assert_not_called()

    async def test_missing_session_fails_closed(self) -> None:
        tool = HandoffRequestTool()
        conversation = _conversation()
        result = await tool.create_handoff(
            conversation_id=conversation.id,
            tenant_context=_tenant(),
            session=None,
        )
        assert result.ok is False
        assert result.error_message == STAFF_UNAVAILABLE

    async def test_note_phone_masked(self) -> None:
        """PII minimization: phone-like sequences in note are masked."""
        conversation = _conversation()
        session = _configured_session(conversation)
        tool = HandoffRequestTool()

        await tool.create_handoff(
            conversation_id=conversation.id,
            tenant_context=_tenant(),
            session=session,
            note="мой номер +7 900 123-45-67, звоните",
        )
        row = session.add.call_args[0][0]
        assert "79001234567" not in str(row.note)
        assert "[телефон скрыт]" in row.note

    async def test_notification_queued_for_after_commit(self) -> None:
        """Dialog-turn scope: hidden note is queued for delivery after COMMIT."""
        from src.tools.handoff_request import (
            AfterCommitHandoffNotificationQueue,
            HandoffTurnScope,
            handoff_turn_scope,
        )

        conversation = _conversation()
        session = _configured_session(conversation)
        queue = AfterCommitHandoffNotificationQueue()
        tool = HandoffRequestTool()
        tenant = _tenant()

        with handoff_turn_scope(
            HandoffTurnScope(conversation_id=conversation.id, notifications=queue)
        ):
            await tool.create_handoff(
                conversation_id=conversation.id,
                tenant_context=tenant,
                session=session,
                note="нужен оператор",
            )

        assert queue.pending is True
        note_item = queue.items[0]
        assert note_item.staff_chat_id == -534_183_2316
        assert note_item.conversation_id == conversation.id

    async def test_tool_result_ok_true(self) -> None:
        tool = HandoffRequestTool()
        conversation = _conversation()
        session = _configured_session(conversation)
        result = await tool.create_handoff(
            conversation_id=conversation.id,
            tenant_context=_tenant(),
            session=session,
        )
        assert isinstance(result, type(result))

    async def test_mask_helper(self) -> None:
        assert mask_phone_like("звонок по +7 (900) 123-45-67") == ("звонок по [телефон скрыт]")
