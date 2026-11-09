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
from src.db.repositories.repositories import (
    AgentRepository,
    ConversationRepository,
    CustomerRepository,
    MessageRepository,
)
from src.monitoring.logging import get_logger
from src.telegram_adapter.schemas import IncomingMessage

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


def _build_default_agent_loop() -> AgentLoop:
    """Default loop: provider from platform settings + stub tool registry.

    The stub registry is replaced by real tools in Stages 6-7
    (docs/DEVELOPMENT_PLAN.md Stage 5).
    """
    from src.agent_core.agent_loop import AgentLoop
    from src.config.settings import get_settings
    from src.llm_providers.factory import LLMProviderFactory
    from src.tools.stubs import build_default_stub_registry

    provider = LLMProviderFactory.from_settings(get_settings())
    return AgentLoop(provider, build_default_stub_registry())


class ProcessIncomingMessageUseCase:
    """Orchestrates message ingestion, customer upsert, conversation, agent turn."""

    def __init__(self, session: AsyncSession, agent_loop: AgentLoop | None = None) -> None:
        """`agent_loop=None` builds the default loop from platform settings."""
        self.session = session
        self.agent_loop = agent_loop if agent_loop is not None else _build_default_agent_loop()

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

        return ProcessMessageResult(
            tenant_id=message.tenant_id,
            customer_id=customer.id,
            conversation_id=conversation.id,
            message_id=user_message.id,
            response_text=response_text,
            is_duplicate=False,
        )
