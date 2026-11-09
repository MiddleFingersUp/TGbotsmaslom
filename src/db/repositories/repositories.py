"""Domain-specific repositories implementing tenant isolation rules."""

import datetime
import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy import text as sqlalchemy_text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.context import TenantContext
from src.db.models.agents import Agent
from src.db.models.bots import Bot
from src.db.models.conversations import Conversation
from src.db.models.customers import Customer
from src.db.models.enums import ServiceStatus
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
    """Service catalog repository with Russian full-text search."""

    def __init__(self, session: AsyncSession, tenant_context: TenantContext) -> None:
        super().__init__(Service, session, tenant_context)

    async def fulltext_search(
        self,
        query: str,
        tags: list[str],
        top_k: int,
        *,
        today: datetime.date | None = None,
    ) -> Sequence[Service]:
        """Rank active services by PostgreSQL full-text search (`russian` config).

        Implementation notes (docs/ARCHITECTURE.md §10, docs/DATABASE.md §4.4):
        - the search index column from DATABASE.md (`search_tsv`) is not
          present in the current schema/migration, so the identical
          expression is computed in-query; when a GENERATED column lands,
          only this expression needs to change;
        - tenant isolation is structural: `tenant_id` comes from
          TenantContext, never from arguments;
        - only ACTIVE services, not expired (`valid_until >= today` or NULL);
        - every requested tag must be present (ARRAY containment, AND).
        """
        cleaned = query.strip()
        if not cleaned:
            return []

        # 'russian'::regconfig as an inline SQL literal (not a bound param):
        # keeps the identical generated SQL rendered by DATABASE.md §4.4 and
        # lets query compilation work without a REGCONFIG literal renderer.
        russian_config = sqlalchemy_text("'russian'::regconfig")
        search_tsv = func.to_tsvector(
            russian_config, func.concat(Service.name, " ", Service.description)
        )
        tsquery = func.plainto_tsquery(russian_config, cleaned)
        cutoff_date = today or datetime.date.today()

        statement = (
            select(Service)
            .where(
                Service.tenant_id == self.tenant_id,
                Service.status == ServiceStatus.ACTIVE,
                search_tsv.op("@@")(tsquery),
            )
            .where(Service.valid_until.is_(None) | (Service.valid_until >= cutoff_date))
            .order_by(func.ts_rank(search_tsv, tsquery).desc(), Service.name.asc())
            .limit(max(1, min(int(top_k), 10)))
        )
        for tag in tags:
            statement = statement.where(Service.tags.contains([tag]))
        rows = await self.session.execute(statement)
        return rows.scalars().all()


class FAQRepository(TenantRepository[FAQ]):
    """FAQ knowledge repository with Russian full-text search."""

    def __init__(self, session: AsyncSession, tenant_context: TenantContext) -> None:
        super().__init__(FAQ, session, tenant_context)

    async def fulltext_search(self, query: str, top_k: int) -> Sequence[FAQ]:
        """Rank active FAQ entries by full-text match over question+answer."""
        cleaned = query.strip()
        if not cleaned:
            return []

        russian_config = sqlalchemy_text("'russian'::regconfig")
        search_tsv = func.to_tsvector(russian_config, func.concat(FAQ.question, " ", FAQ.answer))
        tsquery = func.plainto_tsquery(russian_config, cleaned)

        statement = (
            select(FAQ)
            .where(
                FAQ.tenant_id == self.tenant_id,
                FAQ.status == ServiceStatus.ACTIVE,
                search_tsv.op("@@")(tsquery),
            )
            .order_by(func.ts_rank(search_tsv, tsquery).desc(), FAQ.question.asc())
            .limit(max(1, min(int(top_k), 10)))
        )
        rows = await self.session.execute(statement)
        return rows.scalars().all()


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
