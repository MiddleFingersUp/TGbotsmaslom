"""Use case for atomic transactional processing of incoming client messages."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
from src.telegram_adapter.schemas import IncomingMessage


@dataclass(frozen=True)
class ProcessMessageResult:
    """Result of processing an incoming client message."""

    tenant_id: UUID
    customer_id: UUID
    conversation_id: UUID
    message_id: UUID
    response_text: str
    is_duplicate: bool = False


class ProcessIncomingMessageUseCase:
    """Orchestrates message ingestion, customer upsert, conversation retrieval, and response."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

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

        # ─── 4. Determine Response Logic (Echo / Start / Media) ────────
        text_content = (message.text or "").strip()
        response_text: str

        if message.is_media:
            # Per FR-1.4: polite refusal asking for text
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
        else:
            # Phase 3 echo stub: will be replaced by Agent Core in Phase 5
            response_text = f"Вы написали: {text_content}"

        # ─── 5. Persist User Message & Update Conversation ────────────
        user_message = Message(
            tenant_id=message.tenant_id,
            conversation_id=conversation.id,
            role=MessageRole.USER,
            text=text_content or "[Медиасообщение]",
            telegram_message_id=message.telegram_message_id,
            created_at=now,
        )
        await message_repo.add(user_message)

        # Persist Agent Response
        agent_message = Message(
            tenant_id=message.tenant_id,
            conversation_id=conversation.id,
            role=MessageRole.AGENT,
            text=response_text,
            created_at=now,
        )
        await message_repo.add(agent_message)

        conversation.message_count += 2
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
