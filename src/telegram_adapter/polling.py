"""Telegram polling loop (getUpdates) — legitimate pilot entrypoint (Stage 8.3).

Deployment reality: the pilot runs on a Russian VPS WITHOUT a domain/TLS
certificate, so the Telegram webhook cannot be registered yet
(`docs/SECURITY.md` §9.1). The polling loop becomes the production
entrypoint for the pilot and MUST reuse the exact same processing contour
as the webhook endpoint (ARCHITECTURE §13):

- update normalization through the shared ``parse_telegram_update``;
- private chat → ``ProcessIncomingMessageUseCase`` (agent contour, one DB
  session per update, use case commits);
- staff group → ``ProcessStaffReplyUseCase`` (handoff relay, Caller-Bot
  from ``build_default_bot()`` — same as the webhook path).

Delivery guarantees (honest):
- offset is confirmed STRICTLY after an update's processing attempt
  completes (at-least-once); a crash before confirmation makes Telegram
  redeliver the update, and idempotency (``messages.telegram_message_id``
  unique per tenant, staff-reply dedupe) prevents duplicate replies;
- a persistently failing (broken) update must not stop the loop — the
  error is logged WITHOUT PII and the offset still advances;
- network/API failures → exponential backoff 1s → 2s → 4s → … → 60s cap,
  reset on success.

Forbidden here (ARCHITECTURE): no LLM calls outside the agent loop (the
agent loop lives inside the use case), no direct DB queries bypassing the
use cases (the only direct query is the same bot-resolution select the
webhook endpoint already performs), and no ``setWebhook`` calls — only
``deleteWebhook`` at startup (required for ``getUpdates`` to work).
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections import deque
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.process_incoming_message import ProcessIncomingMessageUseCase
from src.config.settings import build_default_bot
from src.db.models.bots import Bot
from src.handoff.use_case import ProcessStaffReplyUseCase
from src.monitoring.logging import get_logger
from src.telegram_adapter.update_parser import parse_telegram_update

logger = get_logger("telegram_polling")

TELEGRAM_API_BASE = "https://api.telegram.org"

_POLL_TIMEOUT_SECONDS = 25  # Telegram long-poll window
_HTTP_TIMEOUT_SECONDS = 35.0  # poll window + network margin
_BACKOFF_START_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 60.0
_SEEN_UPDATE_IDS_LIMIT = 500  # duplicate guard window (belt & braces)


class TelegramApiError(RuntimeError):
    """Telegram Bot API responded with ``ok: false``."""


class TelegramPollingLoop:
    """Long-polling consumer of Telegram updates (single-bot MVP)."""

    def __init__(
        self,
        bot_token: str,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        api_base: str = TELEGRAM_API_BASE,
        backoff_start: float = _BACKOFF_START_SECONDS,
        backoff_cap: float = _BACKOFF_CAP_SECONDS,
    ) -> None:
        """``transport``/``api_base``/``backoff_*`` are test/operator knobs.

        The bot token is never logged; httpx exception messages may contain
        the request URL (which embeds the token), so error logging below
        records ``type(error).__name__`` only — never the message.
        """
        self._token = bot_token
        self._session_factory = session_factory
        self._client = httpx.AsyncClient(
            base_url=api_base, transport=transport, timeout=_HTTP_TIMEOUT_SECONDS
        )
        self._offset = 0
        self._backoff_start = backoff_start
        self._backoff_cap = backoff_cap
        self._stop_event = asyncio.Event()
        self._seen: deque[int] = deque(maxlen=_SEEN_UPDATE_IDS_LIMIT)

    # ─── Lifecycle ──────────────────────────────────────────────────────

    def stop(self) -> None:
        """Request a graceful shutdown (signal handler / test hook)."""
        self._stop_event.set()

    def _install_signal_handlers(self) -> None:
        """SIGINT/SIGTERM → graceful stop (POSIX; Windows: no-op)."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            # Windows Proactor loop does not support signal handlers;
            # CLI Ctrl+C is handled at the entrypoint instead.
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, self.stop)

    async def run(self) -> None:
        """Run the polling loop until a graceful stop is requested."""
        self._install_signal_handlers()
        try:
            await self._delete_webhook()
            logger.info("polling.started", poll_timeout=_POLL_TIMEOUT_SECONDS)
            backoff = self._backoff_start
            while not self._stop_event.is_set():
                try:
                    updates = await self._get_updates()
                except (httpx.HTTPError, TelegramApiError) as error:
                    logger.warning(
                        "polling.transport_error",
                        error_type=type(error).__name__,
                        backoff=backoff,
                    )
                    await self._backoff_sleep(backoff)
                    backoff = min(backoff * 2, self._backoff_cap)
                    continue
                backoff = self._backoff_start
                for update in updates:
                    if self._stop_event.is_set():
                        break
                    await self._process_update(update)
                # Yield control even when the transport responds instantly
                # (fast mocks in tests): without a suspension point this
                # coroutine would starve the event loop.
                await asyncio.sleep(0)
        finally:
            await self._client.aclose()
            logger.info("polling.stopped")

    async def _backoff_sleep(self, seconds: float) -> None:
        """Sleep for the backoff window, aborting early on stop signal."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)

    # ─── Telegram API ───────────────────────────────────────────────────

    async def _api(self, method: str, payload: dict[str, Any]) -> Any:
        """Call one Bot API method; raises on transport or ``ok: false``."""
        response = await self._client.post(f"/bot{self._token}/{method}", json=payload)
        data = response.json()
        if not data.get("ok"):
            raise TelegramApiError(method)
        return data.get("result")

    async def _delete_webhook(self) -> None:
        """deleteWebhook at startup — required for getUpdates to return data."""
        try:
            await self._api("deleteWebhook", {"drop_pending_updates": False})
        except (httpx.HTTPError, TelegramApiError) as error:
            logger.warning("polling.delete_webhook_failed", error_type=type(error).__name__)

    async def _get_updates(self) -> list[dict[str, Any]]:
        """One long-poll batch (only ``message`` updates are requested)."""
        result = await self._api(
            "getUpdates",
            {
                "timeout": _POLL_TIMEOUT_SECONDS,
                "offset": self._offset,
                "allowed_updates": ["message"],
            },
        )
        return list(result or [])

    # ─── Update processing ──────────────────────────────────────────────

    def _confirm(self, update_id: int) -> None:
        """Advance the getUpdates offset (idempotent, monotonic)."""
        self._seen.append(update_id)
        self._offset = max(self._offset, update_id + 1)

    async def _process_update(self, update: dict[str, Any]) -> None:
        """Handle one update; the offset advances in ``finally``.

        Strictly after the processing attempt: a crash mid-processing leaves
        the offset unchanged → Telegram redelivers → idempotency (unique
        ``telegram_message_id``) prevents duplicated replies. A logged
        processing error still advances the offset so the loop never gets
        stuck on one broken update.
        """
        update_id = update.get("update_id")
        if not isinstance(update_id, int):
            logger.warning("polling.update_without_id")
            return

        if update_id in self._seen or update_id < self._offset:
            # Redelivered/older update: advance past it without reprocessing.
            self._confirm(update_id)
            return

        try:
            async with self._session_factory() as session:
                await self._route(update, session)
        except Exception as error:  # noqa: BLE001 — one broken update ≠ loop death
            logger.error(
                "polling.processing_failed",
                update_id=update_id,
                error_type=type(error).__name__,
            )
        finally:
            self._confirm(update_id)

    async def _route(self, update: dict[str, Any], session: AsyncSession) -> None:
        """Route one update through the same contour as the webhook."""
        bot = await self._resolve_bot(session)
        incoming = parse_telegram_update(update, bot) if bot is not None else None
        if incoming is None:
            logger.info("polling.update_skipped", update_id=update.get("update_id"))
            return

        if incoming.is_staff_group:
            outcome = await ProcessStaffReplyUseCase(session, bot=build_default_bot()).execute(
                incoming
            )
            logger.info(
                "polling.staff_update",
                update_id=update.get("update_id"),
                status=outcome.status,
            )
            return

        result = await ProcessIncomingMessageUseCase(session).execute(incoming)
        logger.info(
            "polling.update_handled",
            update_id=update.get("update_id"),
            is_duplicate=result.is_duplicate,
        )

    async def _resolve_bot(self, session: AsyncSession) -> Bot | None:
        """Single active DB bot (single-bot MVP pilot, same shape as webhook).

        The webhook endpoint resolves the bot by URL bot_id; polling has no
        URL parameter, so the single active bot row is used. Without an
        active bot the update is skipped fail-closed (no tenant mapping →
        no processing).
        """
        query = select(Bot).where(Bot.is_active.is_(True)).limit(1)
        result = await session.execute(query)
        bot = result.scalars().first()
        if bot is None:
            logger.warning("polling.no_active_bot")
        return bot


async def run_polling() -> int:
    """CLI entrypoint: build the loop from settings and run it to completion."""
    from src.config.settings import get_settings
    from src.db.session import get_session_factory
    from src.monitoring.logging import setup_logging

    settings = get_settings()
    setup_logging(log_level=settings.log_level, json_logs=(settings.app_env != "dev"))

    token = settings.telegram_bot_token
    if not token or not token.strip():
        logger.error("polling.no_bot_token_configured")
        return 2
    loop = TelegramPollingLoop(token.strip(), get_session_factory())
    await loop.run()
    return 0
