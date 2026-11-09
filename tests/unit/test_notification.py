"""Unit tests for the Notifier interface and after-commit ordering (Stage 7.1).

Guarantees (docs/DATABASE.md §7 п. 2, Notifier fail-closed contract):
- `staff_chat_id` set → the transport send IS called;
- `staff_chat_id` NULL → SKIPPED + warn log, transport NOT called, no error;
- transport/transport-level failures are swallowed (no exception escapes);
- POST-COMMIT ordering: the queued notification is sent AFTER the dialog
  turn's `session.commit()`, never inside the transaction — proven with a
  mock session that records the event order.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot

from src.agent_core.agent_loop import AgentLoop
from src.application.process_incoming_message import (
    ProcessIncomingMessageUseCase,
    build_default_registry,
)
from src.db.models.agents import Agent
from src.db.models.conversations import Conversation
from src.db.models.enums import LeadStatus
from src.db.models.tenants import Tenant
from src.leads.use_case import (
    AfterCommitNotificationQueue,
    PendingLeadNotification,
    render_lead_notification,
)
from src.llm_providers.mock import MockLLMProvider, MockReply
from src.llm_providers.schemas import ToolCall
from src.notification.notifier import Notifier, NotifyOutcome
from src.telegram_adapter.schemas import IncomingMessage, UserIdentity


class _RecordingBot:
    """Fake aiogram Bot: records sends, optionally fails on transport."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    async def send_message(self, chat_id: int, text: str) -> None:
        self.calls.append({"chat_id": chat_id, "text": text})
        if self.fail:
            raise RuntimeError("telegram transport down")


def _tenant(staff_chat_id: int | None) -> Tenant:
    tenant = Tenant(name=f"t-{uuid.uuid4()}")
    if staff_chat_id is not None:
        tenant.staff_chat_id = staff_chat_id
    return tenant


@pytest.mark.unit
@pytest.mark.asyncio
class TestNotifierSend:
    async def test_sends_when_staff_chat_configured(self) -> None:
        bot = _RecordingBot()
        notifier = Notifier(bot=bot)  # type: ignore[arg-type]
        outcome = await notifier.send("текст уведомления", _tenant(-123456789))
        assert outcome.sent is True
        assert outcome.status == "sent"
        assert len(bot.calls) == 1
        assert bot.calls[0]["chat_id"] == -123456789
        assert "текст уведомления" in bot.calls[0]["text"]

    async def test_skips_when_staff_chat_unset(self) -> None:
        bot = _RecordingBot()
        notifier = Notifier(bot=bot)  # type: ignore[arg-type]
        outcome = await notifier.send("текст уведомления", _tenant(None))
        assert outcome.sent is False
        assert outcome.skipped is True
        assert outcome.status == "skipped"
        assert bot.calls == []  # decimals/PII never reach the transport

    async def test_transport_error_never_propagates(self) -> None:
        bot = _RecordingBot(fail=True)
        notifier = Notifier(bot=bot)  # type: ignore[arg-type]
        outcome = await notifier.send("текст", _tenant(-123456789))
        assert outcome.sent is False
        assert outcome.skipped is False
        assert outcome.status == "failed"

    async def test_no_bot_fail_closed(self) -> None:
        notifier = Notifier(bot=None)
        outcome = await notifier.send("текст", _tenant(-123456789))
        assert outcome.sent is False
        assert outcome.status == "failed"
        assert outcome.reason == "no_bot"

    async def test_queue_flush_drains_and_renders_without_pii(self) -> None:
        queue = AfterCommitNotificationQueue()
        note = PendingLeadNotification(
            tenant_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            lead_id=uuid.uuid4(),
            status=LeadStatus.NEW,
            intent="Хочу записаться, номер +7 999 000-00-00",
        )
        queue.add(note)
        assert queue.pending is True
        notes = queue.drain()
        assert queue.drain() == []  # drain is idempotent
        text = render_lead_notification(notes[0])
        assert "+7 999 000-00-00" not in text


@pytest.mark.unit
class TestAfterCommitOrdering:
    """The queued notification is flushed AFTER commit, never in the turn."""

    def _agent(self, tenant_id: uuid.UUID) -> Agent:
        return Agent(
            tenant_id=tenant_id,
            name="Sales Assistant",
            system_prompt="Правила компании.",
            welcome_message="Добро пожаловать!",
            max_context_messages=12,
            temperature=0.5,
            allowed_tools=["save_lead"],
            is_active=True,
        )

    def _mock_result(self, value: Any = None) -> MagicMock:
        result = MagicMock()
        scalars = MagicMock()
        scalars.first.return_value = value
        scalars.all.return_value = value if isinstance(value, list) else []
        result.scalars.return_value = scalars
        return result

    @pytest.mark.asyncio
    async def test_notification_sent_after_commit_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tenant_id = uuid.uuid4()
        agent = self._agent(tenant_id)
        order: list[str] = []

        from src.application import process_incoming_message as process_module

        class _RecordingQueue(AfterCommitNotificationQueue):
            def add(self, note: PendingLeadNotification) -> None:
                order.append("queue_add")
                super().add(note)

        # The turn body instantiates the plain queue; intercept THAT class.
        monkeypatch.setattr(process_module, "AfterCommitNotificationQueue", _RecordingQueue)

        class _RecordingNotifier(Notifier):
            def __init__(self) -> None:
                super().__init__(bot=None)
                self.sent: list[NotifyOutcome] = []

            async def send(
                self, text: str, tenant: Tenant, bot: Bot | None = None
            ) -> NotifyOutcome:
                order.append("send")
                outcome = await super().send(text, tenant, bot)
                self.sent.append(outcome)
                return outcome

        added_objects: list[Any] = []

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.add.side_effect = lambda obj: added_objects.append(obj)

        async def _commit_side_effect() -> None:
            order.append("commit")

        mock_session.commit.side_effect = _commit_side_effect
        mock_session.get.side_effect = [  # flush path loads the tenant row
            _tenant(None)  # fixture: staff_chat_id UNSET → honest skipped
        ]

        pre_loop_results = [
            self._mock_result(None),  # idempotency
            self._mock_result(None),  # customer lookup
            self._mock_result(None),  # conversation lookup (None → new conversation)
            self._mock_result(agent),  # active agent
            self._mock_result([]),  # conversation history for the loop
        ]
        call_index = 0

        def _dispense(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_index
            call_index += 1
            if call_index <= len(pre_loop_results):
                return pre_loop_results[call_index - 1]
            if call_index == 6:  # save_lead: _get_conversation → the new dialog
                dialog = [c for c in added_objects if isinstance(c, Conversation)][0]
                return self._mock_result(dialog)
            return self._mock_result(None)  # save_lead: _get_active_lead

        mock_session.execute.side_effect = _dispense

        provider = MockLLMProvider(
            responses=[
                MockReply(
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="save_lead",
                            arguments={
                                "intent": "запись на стрижку",
                                "contact_phone": "+7 999 000-00-00",
                            },
                        )
                    ]
                ),
                "Заявка принята, сотрудник свяжется с вами.",
            ]
        )
        loop = AgentLoop(provider, build_default_registry())
        use_case = ProcessIncomingMessageUseCase(
            mock_session, agent_loop=loop, notifier=_RecordingNotifier()
        )

        incoming = IncomingMessage(
            tenant_id=tenant_id,
            bot_id=uuid.uuid4(),
            telegram_message_id=101,
            user=UserIdentity(
                telegram_user_id=42,
                telegram_chat_id=42,
                first_name="Анна",
            ),
            text="Хочу записаться, мой номер +7 999 000-00-00",
        )
        result = await use_case.execute(incoming)

        # THE ordering guarantee: queue-add happens in the turn body,
        # commit completes BEFORE any send, send is post-commit.
        assert order[:3] == ["queue_add", "commit", "send"]
        assert result.response_text == "Заявка принята, сотрудник свяжется с вами."
        assert result.notifications == ("skipped",)  # staff_chat_id unset
        mock_session.commit.assert_awaited_once()
