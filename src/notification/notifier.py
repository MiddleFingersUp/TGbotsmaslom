"""Staff notification service via Telegram Bot API."""

import structlog
from aiogram import Bot

logger = structlog.get_logger("notifier")


class Notifier:
    """Sends notifications and alerts to tenant staff Telegram chats/groups."""

    def __init__(self, bot: Bot | None = None) -> None:
        self._bot = bot

    async def notify_staff(
        self,
        staff_chat_id: int,
        text: str,
        bot: Bot | None = None,
    ) -> bool:
        """Deliver text notification to a staff chat or group.

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
