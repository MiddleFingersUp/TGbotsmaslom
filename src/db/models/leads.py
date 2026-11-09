"""Lead entity matching DATABASE.md §4.8."""

import datetime
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.db.models.enums import LeadSource, LeadStatus

if TYPE_CHECKING:
    from src.db.models.conversations import Conversation
    from src.db.models.customers import Customer
    from src.db.models.services import Service


class Lead(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Qualified sales lead captured by the agent or staff."""

    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_leads_tenant_status_created", "tenant_id", "status", "created_at"),
        CheckConstraint(
            "contact_phone IS NOT NULL OR contact_username IS NOT NULL",
            name="ck_leads_contact_required",
        ),
        CheckConstraint(
            "closed_at IS NULL OR status IN ('won', 'lost')",
            name="ck_leads_closed_at_status",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("services.id", ondelete="SET NULL"),
        nullable=True,
    )
    contact_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    contact_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    intent: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[LeadStatus] = mapped_column(
        Enum(LeadStatus, name="lead_status", native_enum=True),
        default=LeadStatus.NEW,
        nullable=False,
    )
    source: Mapped[LeadSource] = mapped_column(
        Enum(LeadSource, name="lead_source", native_enum=True),
        default=LeadSource.AGENT,
        nullable=False,
    )
    contacted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    conversation: Mapped["Conversation"] = relationship("Conversation")
    customer: Mapped["Customer"] = relationship("Customer")
    service: Mapped["Service | None"] = relationship("Service")
