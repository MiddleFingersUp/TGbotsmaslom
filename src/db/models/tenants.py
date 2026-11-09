"""Tenant entity model matching DATABASE.md §4.1."""

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.db.models.enums import TenantStatus

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
        Enum(TenantStatus, name="tenant_status", native_enum=True),
        default=TenantStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    default_language: Mapped[str] = mapped_column(String(10), default="ru", nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), default="Europe/Moscow", nullable=False)
    booking_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
