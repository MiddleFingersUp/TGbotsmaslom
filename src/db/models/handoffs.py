"""Human handoff entity matching DATABASE.md §4.11."""

import datetime
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.db.models.enums import HandoffStatus, HandoffTrigger

if TYPE_CHECKING:
    from src.db.models.conversations import Conversation
    from src.db.models.tenants import Tenant


class Handoff(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Staff handoff record for transitioning conversations to human agents."""

    __tablename__ = "handoffs"
    __table_args__ = (
        Index("ix_handoffs_tenant_active", "tenant_id", "status"),
        Index("ix_handoffs_conv_active", "conversation_id", "status"),
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
    trigger: Mapped[HandoffTrigger] = mapped_column(
        Enum(HandoffTrigger, name="handoff_trigger", native_enum=True),
        nullable=False,
    )
    status: Mapped[HandoffStatus] = mapped_column(
        Enum(HandoffStatus, name="handoff_status", native_enum=True),
        default=HandoffStatus.ACTIVE,
        nullable=False,
    )
    staff_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    staff_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    initiated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.UTC),
        nullable=False,
    )
    returned_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    tenant: Mapped["Tenant"] = relationship("Tenant")
    conversation: Mapped["Conversation"] = relationship("Conversation")
