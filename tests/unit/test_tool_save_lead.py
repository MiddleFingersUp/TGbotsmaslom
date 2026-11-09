"""Unit tests for the REAL `save_lead` tool (Stage 7.1; offline, mock session).

Behavioral guarantees:
- happy path → `ToolResult(ok=True, data=...)` with IDENTIFIERS/STATUSES
  only; contact PII (phone) never appears in `data`; no commit happens
  inside the tool (the calling use case owns the transaction);
- input without a contact channel → ok=False (structured, to the LLM);
- session=None → fail-closed generic error;
- missing dialog-turn scope → fail-closed generic error;
- DB failure → ok=False with a generic Russian message, raw error text
  never exposed.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.db.context import TenantContext
from src.db.models.conversations import Conversation
from src.leads.use_case import (
    AfterCommitNotificationQueue,
    LeadTurnScope,
    lead_turn_scope,
)
from src.tools.registry import ToolRegistry, run_tool
from src.tools.save_lead import SaveLeadTool


def _mock_result(value: Any = None) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = value
    result.scalars.return_value = scalars
    return result


def _session(conversation: Conversation | None, lead: Any = None) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.execute.side_effect = [
        _mock_result(conversation),  # _get_conversation
        _mock_result(lead),  # _get_active_lead
    ]
    return session


_TENANT = uuid.uuid4()


def _happy_arguments() -> dict[str, Any]:
    return {
        "intent": "Хочу записаться на стрижку",
        "contact_phone": "+7 999 000-00-00",
    }


def _conversation() -> Conversation:
    return Conversation(tenant_id=_TENANT, customer_id=uuid.uuid4())


@pytest.mark.unit
@pytest.mark.asyncio
class TestRealSaveLeadTool:
    async def test_happy_path_returns_ids_without_pii(self) -> None:
        conversation = _conversation()
        queue = AfterCommitNotificationQueue()
        scope = LeadTurnScope(conversation_id=conversation.id, notifications=queue)
        tool = SaveLeadTool()

        with lead_turn_scope(scope):
            result = await tool.execute(
                tool.input_schema.model_validate(_happy_arguments()),
                TenantContext(tenant_id=_TENANT),
                _session(conversation),
            )

        assert result.ok is True
        data = result.data or {}
        assert data["status"] == "new"
        assert data["is_update"] is False
        assert data["conversation_id"] == str(conversation.id)
        assert isinstance(data["lead_id"], str) and data["lead_id"]  # a valid identifier
        payload = str(data)
        assert "+7 999 000-00-00" not in payload  # PII-minimization in data
        assert "стрижку" not in payload  # raw user text stays out of data
        # the notification was queued (flushed later, after COMMIT)
        assert queue.pending is True

    async def test_no_commit_inside_tool(self) -> None:
        conversation = _conversation()
        scope = LeadTurnScope(
            conversation_id=conversation.id, notifications=AfterCommitNotificationQueue()
        )
        tool = SaveLeadTool()
        session = _session(conversation)

        with lead_turn_scope(scope):
            await tool.execute(
                tool.input_schema.model_validate(_happy_arguments()),
                TenantContext(tenant_id=_TENANT),
                session,
            )

        session.commit.assert_not_awaited()
        session.rollback.assert_not_awaited()

    async def test_missing_session_fails_closed(self) -> None:
        tool = SaveLeadTool()
        result = await tool.execute(
            tool.input_schema.model_validate(_happy_arguments()),
            TenantContext(tenant_id=_TENANT),
            None,
        )
        assert result.ok is False
        assert "Сохранить заявку" in (result.error_message or "")

    async def test_missing_dialog_scope_fails_closed(self) -> None:
        tool = SaveLeadTool()
        result = await tool.execute(
            tool.input_schema.model_validate(_happy_arguments()),
            TenantContext(tenant_id=_TENANT),
            _session(_conversation()),
        )
        assert result.ok is False
        assert "Сохранить заявку" in (result.error_message or "")

    async def test_db_failure_never_leaks_raw_errors(self) -> None:
        session = AsyncMock()
        session.execute.side_effect = RuntimeError("boom-with-internals")
        scope = LeadTurnScope(
            conversation_id=uuid.uuid4(), notifications=AfterCommitNotificationQueue()
        )
        tool = SaveLeadTool()
        with lead_turn_scope(scope):
            result = await tool.execute(
                tool.input_schema.model_validate(_happy_arguments()),
                TenantContext(tenant_id=_TENANT),
                session,
            )
        assert result.ok is False
        message = result.error_message or ""
        assert "Сохранить заявку" in message
        assert "boom" not in message
        assert "internals" not in message

    async def test_invalid_input_via_run_tool_is_structured(self) -> None:
        """No contact channel → registry-level structured failure to the LLM."""
        registry = ToolRegistry()
        registry.register(SaveLeadTool())
        result = await run_tool(
            registry,
            "save_lead",
            {"intent": "запись без контакта"},
            allowed_tools=["save_lead"],
            tenant_context=TenantContext(tenant_id=_TENANT),
            session=None,
        )
        assert result.ok is False
        assert result.error_message is not None
        assert "save_lead" in result.error_message
        assert result.data is None
