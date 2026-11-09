"""Unit tests for SaveLeadUseCase (Stage 7.1; offline, mock sessions).

Covered:
- lead creation (status new, source agent, tenant snapshot);
- DEDUPLICATION: a repeated lead in the same dialog UPDATES the existing
  active lead (no second row, no repeated notification);
- lifecycle transitions: valid chain and denied jumps;
- tenant isolation: tenant filter baked into every query; inserts take
  tenant_id only from TenantContext;
- no self-managed commit (the caller owns the transaction).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.db.context import TenantContext
from src.db.models.conversations import Conversation
from src.db.models.enums import LeadSource, LeadStatus
from src.db.models.leads import Lead
from src.leads.use_case import (
    AfterCommitNotificationQueue,
    LeadInput,
    LeadStatusError,
    PendingLeadNotification,
    SaveLeadUseCase,
    can_transition,
    render_lead_notification,
)

_TENANT_A = uuid.uuid4()
_TENANT_B = uuid.uuid4()


def _mock_result(value: Any = None) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = value
    result.scalars.return_value = scalars
    return result


def _conversation(tenant_id: uuid.UUID) -> Conversation:
    return Conversation(
        tenant_id=tenant_id,
        customer_id=uuid.uuid4(),
    )


def _lead_input(**overrides: Any) -> LeadInput:
    fields: dict[str, Any] = {
        "contact_phone": "+7 999 000-00-00",
        "summary": "Запись на стрижку",
    }
    fields.update(overrides)
    return LeadInput.model_validate(fields)


def _recording_queue() -> tuple[AfterCommitNotificationQueue, list[str]]:
    order: list[str] = []

    class _Queue(AfterCommitNotificationQueue):
        def add(self, note: PendingLeadNotification) -> None:
            order.append("queue_add")
            super().add(note)

    return _Queue(), order


@pytest.mark.unit
@pytest.mark.asyncio
class TestSaveLeadCreation:
    """Happy-path creation inside the caller's transaction."""

    async def test_creates_new_lead_with_tenant_snapshot(self) -> None:
        tenant_id = uuid.uuid4()
        conversation = _conversation(tenant_id)
        session = AsyncMock()
        session.add = MagicMock()
        session.execute.side_effect = [
            _mock_result(conversation),  # _get_conversation
            _mock_result(None),  # _get_active_lead
        ]
        queue, order = _recording_queue()

        use_case = SaveLeadUseCase(session, TenantContext(tenant_id=tenant_id))
        outcome = await use_case.save(
            _lead_input(), conversation_id=conversation.id, notifications=queue
        )

        assert outcome.is_update is False
        assert outcome.status == LeadStatus.NEW
        assert outcome.conversation_id == str(conversation.id)
        leads = [
            call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], Lead)
        ]
        # no second Lead row was staged in the UPDATE branch
        assert len(leads) == 1
        lead = leads[0]
        assert lead.tenant_id == tenant_id  # inserted from TenantContext, never from args
        assert lead.conversation_id == conversation.id
        assert lead.customer_id == conversation.customer_id
        assert lead.intent == "Запись на стрижку"
        assert lead.status == LeadStatus.NEW
        assert lead.source == LeadSource.AGENT
        session.flush.assert_awaited()
        session.commit.assert_not_awaited()  # caller commits the whole turn
        session.rollback.assert_not_awaited()

    async def test_unknown_conversation_fails_without_insert(self) -> None:
        tenant_id = uuid.uuid4()
        session = AsyncMock()
        session.add = MagicMock()
        session.execute.return_value = _mock_result(None)

        use_case = SaveLeadUseCase(session, TenantContext(tenant_id=tenant_id))
        with pytest.raises(LookupError):
            await use_case.save(_lead_input(), conversation_id=uuid.uuid4())

        assert not any(isinstance(call.args[0], Lead) for call in session.add.call_args_list)


@pytest.mark.unit
@pytest.mark.asyncio
class TestLeadDeduplication:
    """Этап 7 критерий: повтор в том же диалоге — обновление, не дубликат."""

    async def test_repeat_updates_existing_lead_no_second_row(self) -> None:
        tenant_id = uuid.uuid4()
        conversation = _conversation(tenant_id)
        existing = Lead(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            customer_id=conversation.customer_id,
            contact_phone="+7 900 111-22-33",
            intent="Старая суть",
            status=LeadStatus.NEW,
        )
        session = AsyncMock()
        session.add = MagicMock()
        session.execute.side_effect = [
            _mock_result(conversation),  # _get_conversation
            _mock_result(existing),  # _get_active_lead → UPDATE branch
        ]
        queue, order = _recording_queue()

        use_case = SaveLeadUseCase(session, TenantContext(tenant_id=tenant_id))
        outcome = await use_case.save(
            _lead_input(contact_phone="+7 999 000-00-00", summary="Новая суть"),
            conversation_id=conversation.id,
            notifications=queue,
        )

        assert outcome.is_update is True
        assert outcome.lead_id == str(existing.id)
        # no second Lead row was staged
        assert not any(isinstance(call.args[0], Lead) for call in session.add.call_args_list)
        # snapshot fields refreshed, status untouched
        assert existing.contact_phone == "+7 999 000-00-00"
        assert existing.intent == "Новая суть"
        assert existing.status == LeadStatus.NEW
        # repeated lead does NOT re-notify staff (same lead)
        assert order == []


@pytest.mark.unit
class TestLeadLifecycle:
    """Status transition map: valid chain and denied jumps."""

    def _lead(self, status: LeadStatus) -> Lead:
        return Lead(
            tenant_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            customer_id=uuid.uuid4(),
            contact_phone="+7 999 000-00-00",
            intent="запись",
            status=status,
        )

    def test_transition_map_is_sequential(self) -> None:
        assert can_transition(LeadStatus.NEW, LeadStatus.IN_PROGRESS)
        assert can_transition(LeadStatus.IN_PROGRESS, LeadStatus.CONTACTED)
        assert can_transition(LeadStatus.CONTACTED, LeadStatus.WON)
        assert can_transition(LeadStatus.CONTACTED, LeadStatus.LOST)
        # jumps are denied
        assert not can_transition(LeadStatus.NEW, LeadStatus.WON)
        assert not can_transition(LeadStatus.NEW, LeadStatus.CONTACTED)
        assert not can_transition(LeadStatus.IN_PROGRESS, LeadStatus.WON)
        # terminal states are immutable
        assert not can_transition(LeadStatus.WON, LeadStatus.LOST)
        assert not can_transition(LeadStatus.LOST, LeadStatus.CONTACTED)

    @pytest.mark.asyncio
    async def test_update_status_marks_timestamps(self) -> None:
        session = AsyncMock()
        use_case = SaveLeadUseCase(session, TenantContext(tenant_id=uuid.uuid4()))

        in_progress = await use_case.update_status(
            self._lead(LeadStatus.NEW), LeadStatus.IN_PROGRESS
        )
        assert in_progress.status == LeadStatus.IN_PROGRESS
        assert in_progress.contacted_at is None

        contacted = await use_case.update_status(in_progress, LeadStatus.CONTACTED)
        assert contacted.contacted_at is not None
        assert contacted.closed_at is None

        lost = await use_case.update_status(contacted, LeadStatus.LOST)
        assert lost.closed_at is not None

    @pytest.mark.asyncio
    async def test_update_status_denies_jumps(self) -> None:
        session = AsyncMock()
        use_case = SaveLeadUseCase(session, TenantContext(tenant_id=uuid.uuid4()))
        lead = self._lead(LeadStatus.NEW)

        with pytest.raises(LeadStatusError):
            await use_case.update_status(lead, LeadStatus.WON)
        # denied transition left the lead untouched
        assert lead.status == LeadStatus.NEW
        assert lead.closed_at is None

    @pytest.mark.asyncio
    async def test_update_status_denies_terminal_churn(self) -> None:
        session = AsyncMock()
        use_case = SaveLeadUseCase(session, TenantContext(tenant_id=uuid.uuid4()))
        won_lead = self._lead(LeadStatus.WON)
        with pytest.raises(LeadStatusError):
            await use_case.update_status(won_lead, LeadStatus.LOST)


@pytest.mark.unit
@pytest.mark.asyncio
class TestTenantIsolation:
    """Every query carries the TenantContext tenant filter (§9)."""

    async def test_queries_filter_by_context_tenant(self) -> None:
        """Tenant filter present; no other tenant's id anywhere in params."""
        session = AsyncMock()
        session.execute.return_value = _mock_result(None)

        use_case = SaveLeadUseCase(session, TenantContext(tenant_id=_TENANT_A))
        await use_case._get_conversation(uuid.uuid4())  # noqa: SLF001
        await use_case._get_active_lead(uuid.uuid4())  # noqa: SLF001

        queries = [call.args[0] for call in session.execute.call_args_list]
        assert len(queries) == 2
        for query in queries:
            values = {str(v) for v in query.compile().params.values()}
            assert str(_TENANT_A) in values
            assert str(_TENANT_B) not in values


@pytest.mark.unit
class TestNotificationPayload:
    """Queued notification text never contains contact PII."""

    def test_render_contains_no_phone(self) -> None:
        note = PendingLeadNotification(
            tenant_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            lead_id=uuid.uuid4(),
            status=LeadStatus.NEW,
            intent="Запись на стрижку, телефон +7 999 000-00-00",
        )
        text = render_lead_notification(note)
        # intent is truncated, but the RAW queued phone snapshot must not
        # leak: the queued text keeps identifiers + the trimmed intent.
        assert "+7 999 000-00-00" not in text
        assert str(note.conversation_id)[:8] in text
        assert "new" in text
