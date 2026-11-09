"""Normalized domain schemas for Telegram updates and messages."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class UserIdentity(BaseModel):
    """Normalized identity of the external user."""

    telegram_user_id: int
    telegram_chat_id: int
    username: str | None = None
    first_name: str | None = None
    language_code: str | None = None


class IncomingMessage(BaseModel):
    """Normalized incoming message from Telegram update.

    `is_staff_group=True` marks group updates routed to the staff-handoff
    use case (chat id < 0); they never enter the client agent path.
    """

    tenant_id: UUID
    bot_id: UUID
    telegram_message_id: int
    user: UserIdentity
    text: str | None = None
    is_media: bool = False
    media_type: str | None = None
    is_staff_group: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    raw_update: dict[str, Any] | None = None


class OutgoingMessage(BaseModel):
    """Normalized outgoing message response."""

    chat_id: int
    text: str
    reply_to_message_id: int | None = None
