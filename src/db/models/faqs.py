"""Frequently Asked Questions entity matching DATABASE.md §4.12."""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.db.models.enums import ServiceStatus, pg_enum_values

if TYPE_CHECKING:
    from src.db.models.tenants import Tenant


class FAQ(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Tenant frequently asked questions knowledge unit."""

    __tablename__ = "faqs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "question", name="uq_faqs_tenant_question"),
        Index("ix_faqs_tenant_status", "tenant_id", "status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    status: Mapped[ServiceStatus] = mapped_column(
        Enum(
            ServiceStatus,
            name="service_status",
            native_enum=True,
            values_callable=pg_enum_values,
        ),
        default=ServiceStatus.ACTIVE,
        nullable=False,
    )

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="faqs")
