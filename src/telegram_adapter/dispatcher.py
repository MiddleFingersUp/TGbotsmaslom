"""Aiogram 3 Dispatcher setup and configuration."""

from aiogram import Dispatcher

from src.telegram_adapter.routers import router as messages_router


def create_dispatcher() -> Dispatcher:
    """Create and configure aiogram 3 Dispatcher."""
    dp = Dispatcher()
    dp.include_router(messages_router)
    return dp
