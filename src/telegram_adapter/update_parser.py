"""Shared Telegram update parser (Stage 8.3).

Single normalization point used by BOTH entrypoints (webhook endpoint and
the polling loop) so the processing contour is identical: raw Telegram
update dict → ``IncomingMessage`` or ``None`` (skip). No business logic
lives here — routing stays in the callers.
"""

from __future__ import annotations

from typing import Any

from src.db.models.bots import Bot
from src.telegram_adapter.schemas import IncomingMessage, UserIdentity

# Media shapes we detect (no processing in MVP — polite text-only reply).
_MEDIA_KEYS: tuple[str, ...] = (
    "photo",
    "voice",
    "video",
    "audio",
    "document",
    "sticker",
    "video_note",
)


def parse_telegram_update(update: dict[str, Any], bot: Bot) -> IncomingMessage | None:
    """Normalize one Telegram update into ``IncomingMessage``.

    ``bot`` is the DB bot row that owns the update (webhook: resolved by
    URL bot_id; polling: the single active bot). Returns ``None`` when the
    update must be skipped: non-message updates (inline_query, callback…),
    payloads without ``from``/``chat``/ids, or unknown shapes.
    """
    msg_obj = update.get("message")
    if not isinstance(msg_obj, dict):
        return None

    from_user = msg_obj.get("from")
    chat = msg_obj.get("chat")
    if not isinstance(from_user, dict) or not isinstance(chat, dict):
        return None

    user_id = from_user.get("id")
    chat_id = chat.get("id")
    message_id = msg_obj.get("message_id")
    if not isinstance(user_id, int) or not isinstance(message_id, int):
        return None
    if not isinstance(chat_id, int):
        return None

    text_content = msg_obj.get("text")
    is_media = text_content is None and any(key in msg_obj for key in _MEDIA_KEYS)

    return IncomingMessage(
        tenant_id=bot.tenant_id,
        bot_id=bot.id,
        telegram_message_id=message_id,
        user=UserIdentity(
            telegram_user_id=user_id,
            telegram_chat_id=chat_id,
            username=from_user.get("username"),
            first_name=from_user.get("first_name"),
            language_code=from_user.get("language_code"),
        ),
        text=text_content,
        is_media=is_media,
        is_staff_group=chat_id < 0,
        raw_update=update,
    )
