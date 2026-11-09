"""Use case for atomic transactional processing of incoming client messages.

Since Stage 5.2 the plain-text flow runs through Agent Core
(`AgentLoop`): the LLM (with the tenant tool whitelist) generates the
reply, validated and persisted, replacing the Phase 3 echo stub.

Transaction rules are unchanged (docs/DATABASE.md §7):
- everything happens in the caller's session; the use case commits once;
- the user message is persisted BEFORE the LLM turn so the context
  builder sees it; webhook idempotency (telegram_message_id) preserved;
- special cases (`/start`, `/help`, media) keep deterministic backend
  texts and never reach the LLM (FR-1.4, welcome-message rule).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent_core.state_machine import transition
from src.db.context import TenantContext
from src.db.models.conversations import Conversation
from src.db.models.customers import Customer
from src.db.models.enums import ConversationState, MessageRole
from src.db.models.messages import Message
from src.db.models.tenants import Tenant
from src.db.repositories.repositories import (
    AgentRepository,
    ConversationRepository,
    CustomerRepository,
    MessageRepository,
)
from src.leads.use_case import (
    AfterCommitNotificationQueue,
    LeadTurnScope,
    lead_turn_scope,
    render_lead_notification,
)
from src.monitoring.logging import get_logger
from src.notification.notifier import Notifier
from src.telegram_adapter.schemas import IncomingMessage
from src.tools.registry import ToolRegistry
from src.tools.save_lead import SaveLeadTool
from src.tools.search_faq import SearchFAQTool
from src.tools.search_services import SearchServicesTool
from src.tools.stubs import HandoffRequestTool

if TYPE_CHECKING:
    from src.agent_core.agent_loop import AgentLoop

logger = get_logger("application.process_incoming_message")


@dataclass(frozen=True)
class ProcessMessageResult:
    """Result of processing an incoming client message."""

    tenant_id: UUID
    customer_id: UUID
    conversation_id: UUID
    message_id: UUID
    response_text: str
    is_duplicate: bool = False
    # Post-commit lead notification outcomes ("sent"/"skipped"/"failed").
    notifications: tuple[str, ...] = ()


def build_default_registry() -> ToolRegistry:
    """Production tool registry (docs/DEVELOPMENT_PLAN.md Stage 7.1).

    REAL tools: search_services / search_faq (Stage 6.2 retrieval) and
    save_lead (Stage 7.1 lead capture + after-commit notifications).
    The handoff_request STUB remains until Stage 7.2 (unchanged contract).
    The Stage-5.1 stub registry stays available for offline unit tests.
    """
    registry = ToolRegistry()
    registry.register(SearchServicesTool())
    registry.register(SearchFAQTool())
    registry.register(SaveLeadTool())
    registry.register(HandoffRequestTool())
    return registry


def _build_default_agent_loop() -> AgentLoop:
    """Default loop: provider from platform settings + the production registry."""
    from src.agent_core.agent_loop import AgentLoop
    from src.config.settings import get_settings
    from src.llm_providers.factory import LLMProviderFactory

    provider = LLMProviderFactory.from_settings(get_settings())
    return AgentLoop(provider, build_default_registry())


class ProcessIncomingMessageUseCase:
    """Orchestrates message ingestion, customer upsert, conversation, agent turn."""

    def __init__(
        self,
        session: AsyncSession,
        agent_loop: AgentLoop | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        """`agent_loop=None` builds the default loop from platform settings.

        `notifier` is injectable for unit tests (a stub sender); the real
        Notifier delivers to `tenants.staff_chat_id` AFTER the commit of
        the whole turn, never inside the transaction.
        """
        self.session = session
        self.agent_loop = agent_loop if agent_loop is not None else _build_default_agent_loop()
        self.notifier = notifier if notifier is not None else Notifier()

    async def execute(self, message: IncomingMessage) -> ProcessMessageResult:
        """Process incoming message atomically in a database transaction."""
        tenant_context = TenantContext(tenant_id=message.tenant_id)

        customer_repo = CustomerRepository(self.session, tenant_context)
        conversation_repo = ConversationRepository(self.session, tenant_context)
        message_repo = MessageRepository(self.session, tenant_context)
        agent_repo = AgentRepository(self.session, tenant_context)

        # ─── 1. Idempotency check ─────────────────────────────────────
        # Per DATABASE.md §7: verify if this telegram_message_id was already persisted
        existing_msg_query = select(Message).where(
            Message.tenant_id == message.tenant_id,
            Message.telegram_message_id == message.telegram_message_id,
        )
        existing_msg_res = await self.session.execute(existing_msg_query)
        existing_msg = existing_msg_res.scalars().first()

        if existing_msg is not None:
            return ProcessMessageResult(
                tenant_id=message.tenant_id,
                customer_id=existing_msg.conversation.customer_id,
                conversation_id=existing_msg.conversation_id,
                message_id=existing_msg.id,
                response_text="",
                is_duplicate=True,
            )

        now = datetime.now(UTC)

        # ─── 2. Upsert Customer ───────────────────────────────────────
        customer = await customer_repo.get_by_telegram_user_id(message.user.telegram_user_id)
        if customer is None:
            customer = Customer(
                tenant_id=message.tenant_id,
                telegram_user_id=message.user.telegram_user_id,
                telegram_chat_id=message.user.telegram_chat_id,
                username=message.user.username,
                first_name=message.user.first_name,
                language_code=message.user.language_code,
                first_seen_at=now,
                last_seen_at=now,
            )
            customer = await customer_repo.add(customer)
        else:
            customer.last_seen_at = now
            if message.user.username:
                customer.username = message.user.username
            if message.user.first_name:
                customer.first_name = message.user.first_name
            await self.session.flush()

        # ─── 3. Get or Create Active Conversation ─────────────────────
        conversation = await conversation_repo.get_latest_for_customer(customer.id)
        active_agent = await agent_repo.get_active_agent()

        if conversation is None or conversation.state == ConversationState.CLOSED:
            conversation = Conversation(
                tenant_id=message.tenant_id,
                customer_id=customer.id,
                agent_id=active_agent.id if active_agent else None,
                state=ConversationState.GREETING,
                started_at=now,
                last_message_at=now,
                message_count=0,
            )
            conversation = await conversation_repo.add(conversation)

        # ─── 4. Persist User Message (before the LLM turn) ────────────
        text_content = (message.text or "").strip()
        user_message = Message(
            tenant_id=message.tenant_id,
            conversation_id=conversation.id,
            role=MessageRole.USER,
            text=text_content or "[Медиасообщение]",
            telegram_message_id=message.telegram_message_id,
            created_at=now,
        )
        await message_repo.add(user_message)

        # ─── 5. Produce the Agent Reply ───────────────────────────────
        response_text: str
        message_meta: dict[str, Any] | None = None
        notification_queue: AfterCommitNotificationQueue | None = None

        if message.is_media:
            # Per FR-1.4: polite refusal asking for text, never reaches the LLM
            response_text = (
                "К сожалению, я пока не умею обрабатывать медиафайлы или голосовые сообщения. "
                "Пожалуйста, опишите ваш вопрос текстом."
            )
        elif text_content == "/start":
            if active_agent and active_agent.welcome_message:
                response_text = active_agent.welcome_message
            else:
                response_text = "Здравствуйте! Чем я могу вам помочь?"
        elif text_content == "/help":
            response_text = (
                "Я виртуальный ассистент компании. "
                "Вы можете задать любой вопрос о наших услугах, ценах "
                "или записаться на консультацию."
            )
        elif active_agent is None:
            # No active agent config (e.g. fixture data): deterministic save-and-callback.
            response_text = "Здравствуйте! Ваш запрос сохранён — сотрудник свяжется с вами."
        elif conversation.handoff_active:
            # ARCHITECTURE §13: dialog belongs to the staff; NO LLM call,
            # the user text stays as a user message awaiting a human reply.
            response_text = ""
        else:
            # Stage 7.1: the dialog-turn scope carries the backend-owned
            # conversation id and the after-commit notification queue to
            # the real `save_lead` tool (registry protocol unchanged).
            notification_queue = AfterCommitNotificationQueue()
            scope = LeadTurnScope(
                conversation_id=conversation.id,
                notifications=notification_queue,
            )
            with lead_turn_scope(scope):
                turn = await self.agent_loop.run(
                    self.session,
                    tenant_context,
                    active_agent,
                    conversation,
                )
            response_text = turn.response_text
            message_meta = turn.meta()
            message_meta["ai_model"] = turn.ai_model
            conversation.state = turn.new_state
            if turn.handoff_requested:
                conversation.handoff_active = True

        if response_text:
            greeting_transition = transition(conversation.state, [], had_agent_reply=True)
            conversation.state = greeting_transition
            agent_message = Message(
                tenant_id=message.tenant_id,
                conversation_id=conversation.id,
                role=MessageRole.AGENT,
                text=response_text,
                ai_model=(message_meta or {}).get("ai_model"),
                meta=message_meta,
                created_at=now,
            )
            await message_repo.add(agent_message)
            conversation.message_count += 2
        else:
            # No agent reply produced (handoff mode): user message kept for staff.
            conversation.message_count += 1
        conversation.last_message_at = now
        await self.session.commit()

        # ─── 6. Lead notifications — AFTER COMMIT (DATABASE.md §7 п. 2) ─
        notification_statuses = await self._flush_lead_notifications(
            notification_queue, message.tenant_id
        )

        return ProcessMessageResult(
            tenant_id=message.tenant_id,
            customer_id=customer.id,
            conversation_id=conversation.id,
            message_id=user_message.id,
            response_text=response_text,
            is_duplicate=False,
            notifications=notification_statuses,
        )

    async def _flush_lead_notifications(
        self,
        queue: AfterCommitNotificationQueue | None,
        tenant_id: UUID,
    ) -> tuple[str, ...]:
        """Flush the queued lead notifications after the turn's COMMIT.

        Fail-closed: delivery problems are reduced to status strings; the
        dialog result never depends on the notification outcome. With no
        Bot instance wired (Stage 7.2 will pass the real one through the
        Telegram adapter) the Notifier reports honestly per-item.
        """
        if queue is None:
            return ()
        notes = queue.drain()
        if not notes:
            return ()

        tenant = await self.session.get(Tenant, tenant_id)
        if tenant is None:
            logger.warning("lead.notification.tenant_missing", tenant_id=str(tenant_id))
            return tuple("failed" for _ in notes)

        statuses: list[str] = []
        for note in notes:
            text = render_lead_notification(note)
            try:
                outcome = await self.notifier.send(text, tenant)
            except Exception as error:  # noqa: BLE001 - notifications never break dialogs
                logger.warning(
                    "lead.notification.send_failed",
                    error_type=type(error).__name__,
                    conversation_id=str(note.conversation_id),
                )
                statuses.append("failed")
                continue
            statuses.append(outcome.status)
        return tuple(statuses)
