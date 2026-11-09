"""Domain-specific repositories implementing tenant isolation rules."""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.context import TenantContext
from src.db.models.agents import Agent
from src.db.models.bots import Bot
from src.db.models.conversations import Conversation
from src.db.models.customers import Customer
from src.db.models.faqs import FAQ
from src.db.models.handoffs import Handoff
from src.db.models.leads import Lead
from src.db.models.messages import Message
from src.db.models.services import Service
from src.db.models.tenants import Tenant
from src.db.models.token_usage import TokenUsage
from src.db.repositories.base import BaseRepository, TenantRepository


class TenantRepositoryImpl(BaseRepository[Tenant]):
    """Platform tenant repository."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Tenant, session)

    async def get_by_name(self, name: str) -> Tenant | None:
        """Find tenant by business name."""
        result = await self.session.execute(select(Tenant).where(Tenant.name == name))
        return result.scalars().first()


class BotRepository(TenantRepository[Bot]):
    """Telegram bot repository with fingerprint and active checks."""

    def __init__(self, session: AsyncSession, tenant_context: TenantContext) -> None:
        super().__init__(Bot, session, tenant_context)

    async def get_active_bot(self) -> Bot | None:
        """Get the active bot for current tenant."""
        query = select(Bot).where(
            Bot.tenant_id == self.tenant_id,
            Bot.is_active.is_(True),
        )
        result = await self.session.execute(query)
        return result.scalars().first()


class AgentRepository(TenantRepository[Agent]):
    """AI Agent profile repository."""

    def __init__(self, session: AsyncSession, tenant_context: TenantContext) -> None:
        super().__init__(Agent, session, tenant_context)

    async def get_active_agent(self) -> Agent | None:
        """Get currently active AI agent configuration for tenant."""
        query = select(Agent).where(
            Agent.tenant_id == self.tenant_id,
            Agent.is_active.is_(True),
        )
        result = await self.session.execute(query)
        return result.scalars().first()


class CustomerRepository(TenantRepository[Customer]):
    """Customer entity repository."""

    def __init__(self, session: AsyncSession, tenant_context: TenantContext) -> None:
        super().__init__(Customer, session, tenant_context)

    async def get_by_telegram_user_id(self, telegram_user_id: int) -> Customer | None:
        """Retrieve customer strictly by tenant and telegram user ID."""
        query = select(Customer).where(
            Customer.tenant_id == self.tenant_id,
            Customer.telegram_user_id == telegram_user_id,
        )
        result = await self.session.execute(query)
        return result.scalars().first()


class ConversationRepository(TenantRepository[Conversation]):
    """Conversation dialog repository."""

    def __init__(self, session: AsyncSession, tenant_context: TenantContext) -> None:
        super().__init__(Conversation, session, tenant_context)

    async def get_latest_for_customer(self, customer_id: uuid.UUID) -> Conversation | None:
        """Fetch latest conversation for a customer."""
        query = (
            select(Conversation)
            .where(
                Conversation.tenant_id == self.tenant_id,
                Conversation.customer_id == customer_id,
            )
            .order_by(Conversation.last_message_at.desc())
        )
        result = await self.session.execute(query)
        return result.scalars().first()


class MessageRepository(TenantRepository[Message]):
    """Message repository for loading conversation history."""

    def __init__(self, session: AsyncSession, tenant_context: TenantContext) -> None:
        super().__init__(Message, session, tenant_context)

    async def get_conversation_history(
        self,
        conversation_id: uuid.UUID,
        limit: int = 20,
    ) -> Sequence[Message]:
        """Fetch messages for a conversation ordered chronologically."""
        query = (
            select(Message)
            .where(
                Message.tenant_id == self.tenant_id,
                Message.conversation_id == conversation_id,
            )
            .order_by(Message.created_at.asc())
            .limit(limit)
        )
        result = await self.session.execute(query)
        return result.scalars().all()


class ServiceRepository(TenantRepository[Service]):
    """Service catalog repository."""

    def __init__(self, session: AsyncSession, tenant_context: TenantContext) -> None:
        super().__init__(Service, session, tenant_context)


class FAQRepository(TenantRepository[FAQ]):
    """FAQ knowledge repository."""

    def __init__(self, session: AsyncSession, tenant_context: TenantContext) -> None:
        super().__init__(FAQ, session, tenant_context)


class LeadRepository(TenantRepository[Lead]):
    """Lead repository."""

    def __init__(self, session: AsyncSession, tenant_context: TenantContext) -> None:
        super().__init__(Lead, session, tenant_context)


class HandoffRepository(TenantRepository[Handoff]):
    """Human handoff repository."""

    def __init__(self, session: AsyncSession, tenant_context: TenantContext) -> None:
        super().__init__(Handoff, session, tenant_context)


class TokenUsageRepository(TenantRepository[TokenUsage]):
    """LLM token accounting repository."""

    def __init__(self, session: AsyncSession, tenant_context: TenantContext) -> None:
        super().__init__(TokenUsage, session, tenant_context)
