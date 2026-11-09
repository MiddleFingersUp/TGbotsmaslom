"""AI Agent configuration entity matching DATABASE.md §4.3."""

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from src.db.models.conversations import Conversation
    from src.db.models.tenants import Tenant


class Agent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """AI Agent configuration profile for a tenant."""

    __tablename__ = "agents"
    __table_args__ = (
        Index(
            "uq_agents_tenant_active",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
        CheckConstraint(
            "temperature >= 0 AND temperature <= 2",
            name="ck_agents_temperature_range",
        ),
        CheckConstraint(
            "max_context_messages BETWEEN 2 AND 100",
            name="ck_agents_max_context_messages_range",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(10), default="ru", nullable=False)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    temperature: Mapped[Decimal] = mapped_column(
        Numeric(3, 2),
        default=Decimal("0.50"),
        nullable=False,
    )
    max_context_messages: Mapped[int] = mapped_column(Integer, default=12, nullable=False)
    allowed_tools: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        default=list,
        nullable=False,
    )
    objection_scripts: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    welcome_message: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="agents")
    conversations: Mapped[list["Conversation"]] = relationship(
        "Conversation", back_populates="agent"
    )
