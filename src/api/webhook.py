"""Telegram Webhook HTTP API endpoint."""

import uuid
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings, get_settings
from src.db.models.bots import Bot
from src.db.session import get_db_session
from src.monitoring.logging import get_logger
from src.telegram_adapter.rate_limiter import InMemoryRateLimiter
from src.telegram_adapter.update_parser import parse_telegram_update

router = APIRouter(prefix="/api/v1/telegram")
logger = get_logger("telegram_webhook")


@lru_cache(maxsize=1)
def _rate_limiter_from_settings() -> InMemoryRateLimiter:
    """Build the webhook rate limiter from platform settings (NFR-1/NFR-2).

    Defaults (10 requests / 60 seconds) are equivalent to the previous
    hard-coded values — no security semantics change (Stage 8.2).
    """
    settings = get_settings()
    return InMemoryRateLimiter(
        max_requests=settings.webhook_rate_limit_max,
        window_seconds=settings.webhook_rate_limit_window,
    )


def get_rate_limiter() -> InMemoryRateLimiter:
    """Return the shared webhook rate limiter instance.

    Tests may reconfigure the returned instance in place via
    :func:`configure_rate_limiter` — the endpoint always uses this getter,
    so injected limits are honored without process restart.
    """
    return _rate_limiter_from_settings()


def configure_rate_limiter(
    max_requests: int | None = None, window_seconds: int | None = None
) -> InMemoryRateLimiter:
    """Reconfigure the shared rate limiter (test/operator injection point).

    Only changes the limits of the existing singleton; the 429 semantics and
    response body stay identical. Call without arguments to restore the
    values from ``get_settings()``.
    """
    limiter = _rate_limiter_from_settings()
    settings = get_settings()
    limiter.max_requests = (
        max_requests if max_requests is not None else settings.webhook_rate_limit_max
    )
    limiter.window_seconds = (
        window_seconds if window_seconds is not None else settings.webhook_rate_limit_window
    )
    limiter.reset()
    return limiter


# Module-level alias (kept for existing tests/importers): the same object
# that ``get_rate_limiter()`` returns, so in-place reconfiguration is visible.
rate_limiter = get_rate_limiter()


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

    # ─── 3. Extract Message from Update (shared parser, Stage 8.3) ──
    msg_obj = update_data.get("message")
    if not msg_obj or not isinstance(msg_obj, dict):
        # Ignore non-message updates (e.g. inline_query) gracefully
        return {"ok": True, "detail": "non_message_update_ignored"}

    incoming = parse_telegram_update(update_data, bot)
    if incoming is None:
        return {"ok": True, "detail": "invalid_message_payload"}

    # ─── 4. Rate Limiting (NFR-1/NFR-2, limit from settings) ─────
    if not get_rate_limiter().is_allowed(incoming.user.telegram_user_id):
        logger.warning("User exceeded rate limit", user_id=incoming.user.telegram_user_id)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please slow down.",
        )

    # ─── 5b. Staff-group updates → handoff relay path (ARCHITECTURE §13) ──

    # ─── 5. Dispatch to Use Case (IncomingMessage already built by parser) ──

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
