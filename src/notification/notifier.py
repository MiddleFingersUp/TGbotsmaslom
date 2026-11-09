"""Staff notification service via Telegram Bot API.

Stage 7.1 interface (docs/DEVELOPMENT_PLAN.md Stage 7, задача 2):

- `Notifier.send(text, tenant, bot)` — the single notification entry
  point. The staff chat id comes from `tenants.staff_chat_id`
  (user-approved decision: lead notifications go to the tenant's staff
  chat; for handoffs the value is copied into `handoffs.staff_chat_id`
  in Stage 7.2). A NULL `staff_chat_id` is a NORMAL state: delivery is
  SKIPPED with a warn log and the caller's turn must not break;
- the aiogram `Bot` instance is provided by the caller (no bot token
  handling inside this module; no secrets/logs of it);
- fail-closed: any transport error is logged as a warning and reported
  as a non-sent outcome — exceptions never propagate to the dialog turn;
- delivery happens AFTER the database commit — the caller decides when
  to call this (docs/DATABASE.md §7 п. 2).

Notification texts are built by callers; contacts (phone numbers,
usernames) are NOT logged here and never reach structured logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from aiogram import Bot

if TYPE_CHECKING:
    from src.db.models.tenants import Tenant

logger = structlog.get_logger("notifier")


@dataclass(frozen=True)
class NotifyOutcome:
    """Honest delivery status without exposing internal details."""

    sent: bool
    skipped: bool
    reason: str | None = None

    @property
    def status(self) -> str:
        """Short machine-readable status string for reporting/pipelines."""
        return "sent" if self.sent else ("skipped" if self.skipped else "failed")


class Notifier:
    """Sends notifications and alerts to tenant staff Telegram chats/groups."""

    def __init__(self, bot: Bot | None = None) -> None:
        self._bot = bot

    async def send(self, text: str, tenant: Tenant, bot: Bot | None = None) -> NotifyOutcome:
        """Deliver `text` to the tenant's internal staff chat/group.

        Rules:
        - `tenants.staff_chat_id` NULL → SKIPPED + warn log (normal state:
          the configuration is optional, iteration 7.1 decision);
        - no Bot instance → not sent + warn log (fail-closed);
        - transport/API failures → warn log, no exception escapes.
        """
        staff_chat_id = getattr(tenant, "staff_chat_id", None)
        if staff_chat_id is None:
            logger.warning(
                "notification.skipped_no_staff_chat",
                tenant_id=str(getattr(tenant, "id", "")),
            )
            return NotifyOutcome(sent=False, skipped=True, reason="staff_chat_id_unset")

        active_bot = bot or self._bot
        if active_bot is None:
            logger.warning(
                "notification.no_bot_instance",
                tenant_id=str(getattr(tenant, "id", "")),
            )
            return NotifyOutcome(sent=False, skipped=False, reason="no_bot")

        try:
            await active_bot.send_message(chat_id=staff_chat_id, text=text)
            logger.info(
                "notification.sent",
                tenant_id=str(getattr(tenant, "id", "")),
            )
            return NotifyOutcome(sent=True, skipped=False)
        except Exception as error:  # noqa: BLE001 - fail-closed delivery
            logger.warning(
                "notification.delivery_failed",
                error=type(error).__name__,
                tenant_id=str(getattr(tenant, "id", "")),
            )
            return NotifyOutcome(sent=False, skipped=False, reason="delivery_failed")

    async def notify_staff(
        self,
        staff_chat_id: int,
        text: str,
        bot: Bot | None = None,
    ) -> bool:
        """Stage-3 legacy entry point: direct chat-id delivery.

        Returns True on successful delivery, False on error.
        """
        active_bot = bot or self._bot
        if active_bot is None:
            logger.warning("No bot instance provided for staff notification")
            return False

        try:
            await active_bot.send_message(chat_id=staff_chat_id, text=text)
            logger.info("Staff notification sent successfully", staff_chat_id=staff_chat_id)
            return True
        except Exception as exc:
            logger.error(
                "Failed to deliver staff notification", error=str(exc), staff_chat_id=staff_chat_id
            )
            return False
