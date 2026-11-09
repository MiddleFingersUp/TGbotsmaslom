"""Conversation dialog entity matching DATABASE.md §4.6."""

import datetime
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.db.models.enums import ConversationState

if TYPE_CHECKING:
    from src.db.models.agents import Agent
    from src.db.models.customers import Customer
    from src.db.models.messages import Message


class Conversation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Dialog thread with finite state machine tracking."""

    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_tenant_customer", "tenant_id", "customer_id"),
        Index("ix_conversations_tenant_handoff", "tenant_id", "handoff_active"),
        Index("ix_conversations_last_message", "tenant_id", "last_message_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    state: Mapped[ConversationState] = mapped_column(
        Enum(ConversationState, name="conversation_state", native_enum=True),
        default=ConversationState.GREETING,
        nullable=False,
    )
    handoff_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    context_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_message_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.UTC),
        nullable=False,
    )
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.UTC),
        nullable=False,
    )
    closed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    customer: Mapped["Customer"] = relationship("Customer", back_populates="conversations")
    agent: Mapped["Agent | None"] = relationship("Agent", back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
    )
