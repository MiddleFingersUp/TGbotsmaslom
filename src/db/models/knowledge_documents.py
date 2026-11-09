"""Knowledge base document entity matching DATABASE.md §4.10."""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.db.models.enums import DocumentStatus

if TYPE_CHECKING:
    from src.db.models.tenants import Tenant


class KnowledgeDocument(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Knowledge document holding tenant verified facts and manuals."""

    __tablename__ = "knowledge_documents"
    __table_args__ = (Index("ix_knowledge_docs_tenant_status", "tenant_id", "status"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), default="manual", nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status", native_enum=True),
        default=DocumentStatus.DRAFT,
        nullable=False,
    )

    tenant: Mapped["Tenant"] = relationship("Tenant")
