"""Booking appointment entity matching DATABASE.md §4.9."""

import datetime
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.db.models.enums import BookingStatus, pg_enum_values

if TYPE_CHECKING:
    from src.db.models.conversations import Conversation
    from src.db.models.customers import Customer
    from src.db.models.leads import Lead
    from src.db.models.services import Service


class Booking(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Service appointment booking record with mandatory backend verification constraint."""

    __tablename__ = "bookings"
    __table_args__ = (
        Index("ix_bookings_tenant_status", "tenant_id", "status"),
        Index("ix_bookings_slot", "tenant_id", "slot_at"),
        CheckConstraint(
            "(status = 'confirmed' AND verified_by_backend) OR (status <> 'confirmed')",
            name="ck_bookings_confirmed_must_be_verified",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("services.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("leads.id", ondelete="SET NULL"),
        nullable=True,
    )
    slot_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[BookingStatus] = mapped_column(
        Enum(
            BookingStatus,
            name="booking_status",
            native_enum=True,
            values_callable=pg_enum_values,
        ),
        default=BookingStatus.REQUESTED,
        nullable=False,
    )
    verified_by_backend: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    conversation: Mapped["Conversation | None"] = relationship("Conversation")
    customer: Mapped["Customer"] = relationship("Customer")
    service: Mapped["Service"] = relationship("Service")
    lead: Mapped["Lead | None"] = relationship("Lead")
