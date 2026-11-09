"""Add nullable tenants.staff_chat_id for staff notifications (Stage 7.1).

Revision ID: 002_tenants_staff_chat
Revises: 001_initial_schema
Create Date: 2026-11-10

Decision (user-approved, iteration 7.1): lead notifications are delivered
to the internal Telegram chat/group via `tenants.staff_chat_id`.
The column is NULLABLE on purpose — configuring the staff chat is optional;
until it is set the Notifier skips delivery with a warn log and the agent
turn must not fail. `handoffs.staff_chat_id` (NOT NULL) receives a COPY of
this value at handoff creation (Stage 7.2).

Matching docs/DATABASE.md §4.1 and docs/DEVELOPMENT_PLAN.md Stage 7.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002_tenants_staff_chat"
down_revision: str | None = "001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the optional staff-chat column (no NOT NULL: configuration is optional)."""
    op.add_column(
        "tenants",
        sa.Column("staff_chat_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    """Remove the column; the values are optional configuration, so no
    destructive data migration is required."""
    op.drop_column("tenants", "staff_chat_id")
