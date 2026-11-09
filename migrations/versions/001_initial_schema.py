"""Initial database schema migration creating enums and 13 tenant tables.

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-11-09

Matching docs/DATABASE.md specification.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ─── 1. PostgreSQL ENUM types ─────────────────────────
    tenant_status = postgresql.ENUM(
        "active", "suspended", "archived", name="tenant_status", create_type=False
    )
    service_status = postgresql.ENUM(
        "active", "inactive", "archived", name="service_status", create_type=False
    )
    conversation_state = postgresql.ENUM(
        "greeting",
        "need_discovery",
        "consultation",
        "objection_handling",
        "lead_collection",
        "handoff",
        "closed",
        name="conversation_state",
        create_type=False,
    )
    message_role = postgresql.ENUM(
        "user", "agent", "staff", "system", name="message_role", create_type=False
    )
    lead_status = postgresql.ENUM(
        "new", "in_progress", "contacted", "won", "lost", name="lead_status", create_type=False
    )
    lead_source = postgresql.ENUM(
        "agent", "staff", "operator", name="lead_source", create_type=False
    )
    booking_status = postgresql.ENUM(
        "requested",
        "confirmed",
        "completed",
        "cancelled",
        "failed",
        name="booking_status",
        create_type=False,
    )
    handoff_trigger = postgresql.ENUM(
        "client_request",
        "agent_decision",
        "system_error",
        name="handoff_trigger",
        create_type=False,
    )
    handoff_status = postgresql.ENUM(
        "active", "returned", "closed", name="handoff_status", create_type=False
    )
    document_status = postgresql.ENUM(
        "draft", "published", "archived", name="document_status", create_type=False
    )
    secret_source = postgresql.ENUM(
        "env", "cipher", "vault", name="secret_source", create_type=False
    )

    for enum_type in [
        tenant_status,
        service_status,
        conversation_state,
        message_role,
        lead_status,
        lead_source,
        booking_status,
        handoff_trigger,
        handoff_status,
        document_status,
        secret_source,
    ]:
        enum_type.create(op.get_bind(), checkfirst=True)

    # ─── 2. Table: tenants ─────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("status", tenant_status, server_default="active", nullable=False),
        sa.Column("default_language", sa.String(10), server_default="ru", nullable=False),
        sa.Column("timezone", sa.String(50), server_default="Europe/Moscow", nullable=False),
        sa.Column("booking_enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "retention_days IS NULL OR retention_days BETWEEN 30 AND 1825",
            name="ck_tenants_retention_days_range",
        ),
    )
    op.create_index("ix_tenants_status", "tenants", ["status"])

    # ─── 3. Table: bots ───────────────────────────────────
    op.create_table(
        "bots",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("bot_username", sa.String(255), nullable=False),
        sa.Column("secret_source", secret_source, server_default="env", nullable=False),
        sa.Column("token_ciphertext", sa.Text(), nullable=True),
        sa.Column("token_fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("webhook_secret_ciphertext", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "bot_username", name="uq_bots_tenant_bot_username"),
        sa.CheckConstraint(
            "(secret_source = 'env' AND token_ciphertext IS NULL) OR "
            "(secret_source IN ('cipher', 'vault') AND token_ciphertext IS NOT NULL)",
            name="ck_bots_secret_ciphertext_presence",
        ),
    )
    op.create_index("ix_bots_tenant_id", "bots", ["tenant_id"])

    # ─── 4. Table: agents ─────────────────────────────────
    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("language", sa.String(10), server_default="ru", nullable=False),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("temperature", sa.Numeric(3, 2), server_default="0.50", nullable=False),
        sa.Column("max_context_messages", sa.Integer(), server_default="12", nullable=False),
        sa.Column(
            "allowed_tools",
            postgresql.ARRAY(sa.String()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("objection_scripts", postgresql.JSONB(), nullable=True),
        sa.Column("welcome_message", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "temperature >= 0 AND temperature <= 2", name="ck_agents_temperature_range"
        ),
        sa.CheckConstraint(
            "max_context_messages BETWEEN 2 AND 100", name="ck_agents_max_context_messages_range"
        ),
    )
    op.create_index(
        "uq_agents_tenant_active",
        "agents",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )
    op.create_index("ix_agents_tenant_id", "agents", ["tenant_id"])

    # ─── 5. Table: services ───────────────────────────────
    op.create_table(
        "services",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price_min", sa.Numeric(12, 2), nullable=True),
        sa.Column("price_max", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(3), server_default="RUB", nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.String()), server_default="{}", nullable=False),
        sa.Column("status", service_status, server_default="active", nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_services_tenant_name"),
        sa.CheckConstraint(
            "price_min IS NULL OR price_min >= 0", name="ck_services_price_min_positive"
        ),
        sa.CheckConstraint(
            "price_max IS NULL OR price_max >= 0", name="ck_services_price_max_positive"
        ),
        sa.CheckConstraint(
            "price_min IS NULL OR price_max IS NULL OR price_min <= price_max",
            name="ck_services_price_range_valid",
        ),
        sa.CheckConstraint(
            "duration_minutes IS NULL OR duration_minutes > 0",
            name="ck_services_duration_positive",
        ),
    )
    op.create_index("ix_services_tenant_status", "services", ["tenant_id", "status"])
    op.create_index("ix_services_tags", "services", ["tags"], postgresql_using="gin")

    # ─── 6. Table: customers ──────────────────────────────
    op.create_table(
        "customers",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("first_name", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("language_code", sa.String(10), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "telegram_user_id", name="uq_customers_tenant_tg_user"),
    )
    op.create_index("ix_customers_tenant_last_seen", "customers", ["tenant_id", "last_seen_at"])

    # ─── 7. Table: conversations ──────────────────────────
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            sa.Uuid(),
            sa.ForeignKey("customers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            sa.Uuid(),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("state", conversation_state, server_default="greeting", nullable=False),
        sa.Column("handoff_active", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("context_summary", sa.Text(), nullable=True),
        sa.Column("message_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "last_message_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_conversations_tenant_customer", "conversations", ["tenant_id", "customer_id"]
    )
    op.create_index(
        "ix_conversations_tenant_handoff", "conversations", ["tenant_id", "handoff_active"]
    )
    op.create_index(
        "ix_conversations_last_message", "conversations", ["tenant_id", "last_message_at"]
    )

    # ─── 8. Table: messages ───────────────────────────────
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", message_role, nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("ai_model", sa.String(100), nullable=True),
        sa.Column("meta", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_messages_conv_created", "messages", ["conversation_id", "created_at"])
    op.create_index("ix_messages_tenant_role", "messages", ["tenant_id", "role"])
    op.create_index(
        "uq_messages_tenant_tg_msg",
        "messages",
        ["tenant_id", "telegram_message_id"],
        unique=True,
        postgresql_where=sa.text("telegram_message_id IS NOT NULL"),
    )

    # ─── 9. Table: faqs ───────────────────────────────────
    op.create_table(
        "faqs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.String()), server_default="{}", nullable=False),
        sa.Column("status", service_status, server_default="active", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "question", name="uq_faqs_tenant_question"),
    )
    op.create_index("ix_faqs_tenant_status", "faqs", ["tenant_id", "status"])

    # ─── 10. Table: knowledge_documents ───────────────────
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(50), server_default="manual", nullable=False),
        sa.Column("source_ref", sa.String(255), nullable=True),
        sa.Column("status", document_status, server_default="draft", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_knowledge_docs_tenant_status", "knowledge_documents", ["tenant_id", "status"]
    )

    # ─── 11. Table: leads ─────────────────────────────────
    op.create_table(
        "leads",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            sa.Uuid(),
            sa.ForeignKey("customers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "service_id",
            sa.Uuid(),
            sa.ForeignKey("services.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("contact_phone", sa.String(50), nullable=True),
        sa.Column("contact_username", sa.String(255), nullable=True),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column("status", lead_status, server_default="new", nullable=False),
        sa.Column("source", lead_source, server_default="agent", nullable=False),
        sa.Column("contacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "contact_phone IS NOT NULL OR contact_username IS NOT NULL",
            name="ck_leads_contact_required",
        ),
        sa.CheckConstraint(
            "closed_at IS NULL OR status IN ('won', 'lost')", name="ck_leads_closed_at_status"
        ),
    )
    op.create_index(
        "ix_leads_tenant_status_created",
        "leads",
        ["tenant_id", "status", "created_at"],
    )

    # ─── 12. Table: handoffs ──────────────────────────────
    op.create_table(
        "handoffs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("trigger", handoff_trigger, nullable=False),
        sa.Column("status", handoff_status, server_default="active", nullable=False),
        sa.Column("staff_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("staff_message_id", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "initiated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("returned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_handoffs_tenant_active", "handoffs", ["tenant_id", "status"])
    op.create_index("ix_handoffs_conv_active", "handoffs", ["conversation_id", "status"])

    # ─── 13. Table: bookings ──────────────────────────────
    op.create_table(
        "bookings",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "customer_id",
            sa.Uuid(),
            sa.ForeignKey("customers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "service_id",
            sa.Uuid(),
            sa.ForeignKey("services.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "lead_id", sa.Uuid(), sa.ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("slot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", booking_status, server_default="requested", nullable=False),
        sa.Column("verified_by_backend", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("external_ref", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'confirmed' AND verified_by_backend) OR (status <> 'confirmed')",
            name="ck_bookings_confirmed_must_be_verified",
        ),
    )
    op.create_index("ix_bookings_tenant_status", "bookings", ["tenant_id", "status"])
    op.create_index("ix_bookings_slot", "bookings", ["tenant_id", "slot_at"])

    # ─── 14. Table: token_usage ───────────────────────────
    op.create_table(
        "token_usage",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_token_usage_tenant_created", "token_usage", ["tenant_id", "created_at"])


def downgrade() -> None:
    # Drop tables in reverse order of foreign keys
    op.drop_table("token_usage")
    op.drop_table("bookings")
    op.drop_table("handoffs")
    op.drop_table("leads")
    op.drop_table("knowledge_documents")
    op.drop_table("faqs")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("customers")
    op.drop_table("services")
    op.drop_table("agents")
    op.drop_table("bots")
    op.drop_table("tenants")

    # Drop enum types
    for enum_name in [
        "secret_source",
        "document_status",
        "handoff_status",
        "handoff_trigger",
        "booking_status",
        "lead_source",
        "lead_status",
        "message_role",
        "conversation_state",
        "service_status",
        "tenant_status",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
