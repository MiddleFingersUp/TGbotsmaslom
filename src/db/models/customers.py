"""Customer entity matching DATABASE.md §4.5."""

import datetime
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from src.db.models.conversations import Conversation
    from src.db.models.tenants import Tenant


class Customer(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """End-user client of a tenant business."""

    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "telegram_user_id", name="uq_customers_tenant_tg_user"),
        Index("ix_customers_tenant_last_seen", "tenant_id", "last_seen_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    language_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    first_seen_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.UTC),
        nullable=False,
    )
    last_seen_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.UTC),
        nullable=False,
    )

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="customers")
    conversations: Mapped[list["Conversation"]] = relationship(
        "Conversation", back_populates="customer"
    )
