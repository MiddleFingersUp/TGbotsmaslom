"""Telegram bot mapping entity matching DATABASE.md §4.2."""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.db.models.enums import SecretSource

if TYPE_CHECKING:
    from src.db.models.tenants import Tenant


class Bot(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Telegram bot entity associated with a tenant."""

    __tablename__ = "bots"
    __table_args__ = (
        UniqueConstraint("token_fingerprint", name="uq_bots_token_fingerprint"),
        UniqueConstraint("tenant_id", "bot_username", name="uq_bots_tenant_bot_username"),
        CheckConstraint(
            "(secret_source = 'env' AND token_ciphertext IS NULL) OR "
            "(secret_source IN ('cipher', 'vault') AND token_ciphertext IS NOT NULL)",
            name="ck_bots_secret_ciphertext_presence",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    bot_username: Mapped[str] = mapped_column(String(255), nullable=False)
    secret_source: Mapped[SecretSource] = mapped_column(
        Enum(SecretSource, name="secret_source", native_enum=True),
        default=SecretSource.ENV,
        nullable=False,
    )
    token_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    webhook_secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="bots")
