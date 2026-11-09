"""Service catalog entity matching DATABASE.md §4.4."""

import datetime
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.db.models.enums import ServiceStatus

if TYPE_CHECKING:
    from src.db.models.tenants import Tenant


class Service(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Business service catalog entry."""

    __tablename__ = "services"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_services_tenant_name"),
        Index("ix_services_tenant_status", "tenant_id", "status"),
        Index("ix_services_tags", "tags", postgresql_using="gin"),
        CheckConstraint(
            "price_min IS NULL OR price_min >= 0",
            name="ck_services_price_min_positive",
        ),
        CheckConstraint(
            "price_max IS NULL OR price_max >= 0",
            name="ck_services_price_max_positive",
        ),
        CheckConstraint(
            "price_min IS NULL OR price_max IS NULL OR price_min <= price_max",
            name="ck_services_price_range_valid",
        ),
        CheckConstraint(
            "duration_minutes IS NULL OR duration_minutes > 0",
            name="ck_services_duration_positive",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    price_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="RUB", nullable=False)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    status: Mapped[ServiceStatus] = mapped_column(
        Enum(ServiceStatus, name="service_status", native_enum=True),
        default=ServiceStatus.ACTIVE,
        nullable=False,
    )
    valid_until: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="services")
