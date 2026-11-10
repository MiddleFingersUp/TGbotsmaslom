"""Unit tests for the SYSTEM_ERROR handoff path (Stage 7.2, task 6).

Scenario: a provider failure after the tools/ext context (LLMTimeout) must
trigger the backend handoff procedure directly (never through the LLM)
when the tenant has staff_chat_id configured, and must NOT trigger it when
the tenant is not configured (honest skip) or a handoff already exists.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent_core.agent_loop import AgentLoop
from src.db.models.enums import ConversationState
from src.llm_providers.exceptions import LLMTimeoutError
from src.llm_providers.mock import MockLLMProvider
from src.tools.stubs import build_default_stub_registry
from tests.unit.test_agent_loop import _agent, _conversation, _tenant

pytestmark = [pytest.mark.agent, pytest.mark.unit]


def _session_for_handoff(
    *,
    staff_chat_id: int | None = -534_183_2316,
    active_handoff: bool = False,
    conversation: Any | None = None,
) -> Any:
    """Session stub: tenant lookup (get) + active-handoff lookup (execute)."""
    session = AsyncMock()
    tenant_mock = MagicMock()
    tenant_mock.staff_chat_id = staff_chat_id
    # get side effects: Tenant → Conversation (the backend flag/state flow).
    session.get = AsyncMock(
        side_effect=[tenant_mock, conversation if conversation is not None else MagicMock()]
    )
    if staff_chat_id is None:
        session.get = AsyncMock(return_value=tenant_mock)  # no second lookup (fail closed)

    scalars = MagicMock()
    scalars.first.return_value = MagicMock() if active_handoff else None
    result = MagicMock()
    result.scalars.return_value = scalars
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    return session


@pytest.mark.unit
@pytest.mark.asyncio
async def test_provider_timeout_triggers_system_error_handoff() -> None:
    """LLMTimeout → fallback text + backend handoff row + HANDOFF state."""
    provider = MockLLMProvider(fail_with=LLMTimeoutError("هبوط сети"))
    loop = AgentLoop(provider, build_default_stub_registry())
    conversation = _conversation()
    session = _session_for_handoff(conversation=conversation)

    result = await loop.run(session, _tenant(), _agent(), conversation)

    assert result.used_fallback is True
    assert result.new_state == ConversationState.HANDOFF
    assert conversation.handoff_active is True
    session.add.assert_called_once()  # handoffs row created


@pytest.mark.unit
@pytest.mark.asyncio
async def test_provider_timeout_without_staff_chat_skips_handoff() -> None:
    """Tenant not configured → honest skip: fallback only, no flag, no row."""
    provider = MockLLMProvider(fail_with=LLMTimeoutError("нет сети"))
    loop = AgentLoop(provider, build_default_stub_registry())
    conversation = _conversation()
    session = _session_for_handoff(staff_chat_id=None)

    result = await loop.run(session, _tenant(), _agent(), conversation)

    assert result.used_fallback is True
    assert result.new_state == ConversationState.NEED_DISCOVERY
    assert conversation.handoff_active is False
    session.add.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_existing_active_row_is_idempotent() -> None:
    """Desync case: flag=False, но ACTIVE row существует → no second row.

    The backend procedure is idempotent and re-aligns the flag with the
    existing row (`accepted`, `already_active` semantics).
    """
    provider = MockLLMProvider(fail_with=LLMTimeoutError("сеть недоступна"))
    loop = AgentLoop(provider, build_default_stub_registry())
    conversation = _conversation()
    conversation.handoff_active = False  # desync: flag off, row exists
    session = _session_for_handoff(active_handoff=True, conversation=conversation)

    result = await loop.run(session, _tenant(), _agent(), conversation)

    assert result.used_fallback is True
    assert result.new_state == ConversationState.HANDOFF
    assert conversation.handoff_active is True  # re-aligned by the procedure
    session.add.assert_not_called()  # idempotent: no second handoffs row
