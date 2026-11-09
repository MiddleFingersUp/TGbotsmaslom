"""Tenant entity model matching DATABASE.md §4.1."""

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.db.models.enums import TenantStatus, pg_enum_values

if TYPE_CHECKING:
    from src.db.models.agents import Agent
    from src.db.models.bots import Bot
    from src.db.models.customers import Customer
    from src.db.models.faqs import FAQ
    from src.db.models.services import Service


class Tenant(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Platform tenant representing a client business."""

    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint(
            "retention_days IS NULL OR retention_days BETWEEN 30 AND 1825",
            name="ck_tenants_retention_days_range",
        ),
    )

    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    status: Mapped[TenantStatus] = mapped_column(
        Enum(
            TenantStatus,
            name="tenant_status",
            native_enum=True,
            values_callable=pg_enum_values,
        ),
        default=TenantStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    default_language: Mapped[str] = mapped_column(String(10), default="ru", nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), default="Europe/Moscow", nullable=False)
    booking_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Stage 7.1 decision (docs/DATABASE.md §4.1, migration 002): internal
    # Telegram chat/group id for staff notifications (leads now, handoffs
    # in 7.2 — the value is copied into handoffs.staff_chat_id). NULL is a
    # valid state: Notifier skips delivery with a warn log and the agent
    # turn must never break because of a missing configuration.
    staff_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    bots: Mapped[list["Bot"]] = relationship("Bot", back_populates="tenant", cascade="all, delete")
    agents: Mapped[list["Agent"]] = relationship(
        "Agent", back_populates="tenant", cascade="all, delete"
    )
    services: Mapped[list["Service"]] = relationship(
        "Service", back_populates="tenant", cascade="all, delete"
    )
    customers: Mapped[list["Customer"]] = relationship(
        "Customer", back_populates="tenant", cascade="all, delete"
    )
    faqs: Mapped[list["FAQ"]] = relationship("FAQ", back_populates="tenant", cascade="all, delete")
