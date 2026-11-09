"""Aiogram 3 message routers handling Telegram updates."""

import uuid

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.process_incoming_message import ProcessIncomingMessageUseCase
from src.telegram_adapter.schemas import IncomingMessage, UserIdentity

router = Router(name="telegram_messages")


@router.message(CommandStart())
async def handle_start(
    message: Message, session: AsyncSession, bot_id: uuid.UUID, tenant_id: uuid.UUID
) -> None:
    """Handle /start command."""
    if not message.from_user:
        return

    incoming = IncomingMessage(
        tenant_id=tenant_id,
        bot_id=bot_id,
        telegram_message_id=message.message_id,
        user=UserIdentity(
            telegram_user_id=message.from_user.id,
            telegram_chat_id=message.chat.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            language_code=message.from_user.language_code,
        ),
        text="/start",
    )
    use_case = ProcessIncomingMessageUseCase(session)
    result = await use_case.execute(incoming)
    if result.response_text:
        await message.answer(result.response_text)


@router.message(Command("help"))
async def handle_help(
    message: Message, session: AsyncSession, bot_id: uuid.UUID, tenant_id: uuid.UUID
) -> None:
    """Handle /help command."""
    if not message.from_user:
        return

    incoming = IncomingMessage(
        tenant_id=tenant_id,
        bot_id=bot_id,
        telegram_message_id=message.message_id,
        user=UserIdentity(
            telegram_user_id=message.from_user.id,
            telegram_chat_id=message.chat.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            language_code=message.from_user.language_code,
        ),
        text="/help",
    )
    use_case = ProcessIncomingMessageUseCase(session)
    result = await use_case.execute(incoming)
    if result.response_text:
        await message.answer(result.response_text)


@router.message(F.text)
async def handle_text_message(
    message: Message, session: AsyncSession, bot_id: uuid.UUID, tenant_id: uuid.UUID
) -> None:
    """Handle generic text message."""
    if not message.from_user or not message.text:
        return

    incoming = IncomingMessage(
        tenant_id=tenant_id,
        bot_id=bot_id,
        telegram_message_id=message.message_id,
        user=UserIdentity(
            telegram_user_id=message.from_user.id,
            telegram_chat_id=message.chat.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            language_code=message.from_user.language_code,
        ),
        text=message.text,
    )
    use_case = ProcessIncomingMessageUseCase(session)
    result = await use_case.execute(incoming)
    if result.response_text:
        await message.answer(result.response_text)


@router.message(~F.text)
async def handle_media_message(
    message: Message, session: AsyncSession, bot_id: uuid.UUID, tenant_id: uuid.UUID
) -> None:
    """Handle non-text/media message by asking user for text."""
    if not message.from_user:
        return

    incoming = IncomingMessage(
        tenant_id=tenant_id,
        bot_id=bot_id,
        telegram_message_id=message.message_id,
        user=UserIdentity(
            telegram_user_id=message.from_user.id,
            telegram_chat_id=message.chat.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            language_code=message.from_user.language_code,
        ),
        is_media=True,
        media_type=message.content_type,
    )
    use_case = ProcessIncomingMessageUseCase(session)
    result = await use_case.execute(incoming)
    if result.response_text:
        await message.answer(result.response_text)
