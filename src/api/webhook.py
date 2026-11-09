"""Telegram Webhook HTTP API endpoint."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings, get_settings
from src.db.models.bots import Bot
from src.db.session import get_db_session
from src.monitoring.logging import get_logger
from src.telegram_adapter.rate_limiter import InMemoryRateLimiter
from src.telegram_adapter.schemas import IncomingMessage, UserIdentity

router = APIRouter(prefix="/api/v1/telegram")
logger = get_logger("telegram_webhook")

rate_limiter = InMemoryRateLimiter(max_requests=10, window_seconds=60)


@router.post(
    "/webhook/{bot_id}",
    status_code=status.HTTP_200_OK,
    summary="Telegram Webhook Endpoint",
    description="Receives Telegram updates, verifies secret token, and dispatches message.",
)
async def handle_telegram_webhook(
    bot_id: uuid.UUID,
    update_data: dict[str, Any],
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None, alias="X-Telegram-Bot-Api-Secret-Token"
    ),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Process incoming Telegram update."""
    # ─── 1. Find bot by ID ────────────────────────────────────────
    query = select(Bot).where(Bot.id == bot_id, Bot.is_active.is_(True))
    result = await session.execute(query)
    bot = result.scalars().first()

    if bot is None:
        logger.warning("Webhook received for unknown or inactive bot", bot_id=str(bot_id))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot not found or inactive",
        )

    # ─── 2. Validate Webhook Secret Token ──────────────────────────
    # Expected secret token: either bot.webhook_secret_ciphertext or settings.webhook_secret
    expected_secret = bot.webhook_secret_ciphertext or settings.webhook_secret
    if expected_secret and x_telegram_bot_api_secret_token != expected_secret:
        logger.warning("Invalid webhook secret token", bot_id=str(bot_id))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook secret token",
        )

    # ─── 3. Extract Message from Update ───────────────────────────
    msg_obj = update_data.get("message")
    if not msg_obj or not isinstance(msg_obj, dict):
        # Ignore non-message updates (e.g. inline_query) gracefully
        return {"ok": True, "detail": "non_message_update_ignored"}

    from_user = msg_obj.get("from")
    chat = msg_obj.get("chat")
    if not from_user or not chat:
        return {"ok": True, "detail": "missing_user_or_chat"}

    user_id = from_user.get("id")
    chat_id = chat.get("id")
    message_id = msg_obj.get("message_id")

    if not user_id or not chat_id or not message_id:
        return {"ok": True, "detail": "invalid_message_payload"}

    # ─── 4. Rate Limiting ─────────────────────────────────────────
    if not rate_limiter.is_allowed(user_id):
        logger.warning("User exceeded rate limit", user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please slow down.",
        )

    # ─── 5. Normalize & Dispatch to Use Case ──────────────────────
    text_content = msg_obj.get("text")
    is_media = text_content is None and any(
        k in msg_obj
        for k in (
            "photo",
            "voice",
            "video",
            "audio",
            "document",
            "sticker",
            "video_note",
        )
    )

    incoming = IncomingMessage(
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
        is_staff_group=isinstance(chat_id, int) and chat_id < 0,
        raw_update=update_data,
    )

    # ─── 5b. Staff-group updates → handoff relay path (ARCHITECTURE §13) ──
    # Group messages (chat id < 0, e.g. internal staff chats) NEVER enter
    # the client agent path.
    if incoming.is_staff_group:
        from src.config.settings import build_default_bot
        from src.handoff.use_case import ProcessStaffReplyUseCase

        staff_use_case = ProcessStaffReplyUseCase(session, bot=build_default_bot())
        staff_result = await staff_use_case.execute(incoming)
        return {
            "ok": True,
            "is_duplicate": False,
            "response_text": "",
            "staff_reply": staff_result.status,
        }

    from src.application.process_incoming_message import ProcessIncomingMessageUseCase

    use_case = ProcessIncomingMessageUseCase(session)
    process_result = await use_case.execute(incoming)

    return {
        "ok": True,
        "is_duplicate": process_result.is_duplicate,
        "response_text": process_result.response_text,
    }
