"""Unit tests for Telegram Webhook endpoint security and request handling."""

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from src.api.webhook import rate_limiter
from src.db.models.bots import Bot
from src.db.session import get_db_session


@pytest.mark.unit
async def test_webhook_returns_404_for_unknown_bot(client: AsyncClient, app: Any) -> None:
    """Verify unknown or inactive bot_id results in 404 Not Found."""
    unknown_bot_id = uuid.uuid4()
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = None
    mock_session.execute.return_value = mock_result

    app.dependency_overrides[get_db_session] = lambda: mock_session

    try:
        response = await client.post(
            f"/api/v1/telegram/webhook/{unknown_bot_id}",
            json={"update_id": 1, "message": {"message_id": 10}},
        )
        assert response.status_code == 404
        assert "Bot not found" in response.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.unit
async def test_webhook_rejects_invalid_secret_token(client: AsyncClient, app: Any) -> None:
    """Verify webhook with incorrect X-Telegram-Bot-Api-Secret-Token returns 403 Forbidden."""
    bot_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    mock_bot = Bot(
        id=bot_id,
        tenant_id=tenant_id,
        bot_username="test_bot",
        token_fingerprint="abcdef1234567890",
        webhook_secret_ciphertext="valid-secret-token",
        is_active=True,
    )

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = mock_bot
    mock_session.execute.return_value = mock_result

    app.dependency_overrides[get_db_session] = lambda: mock_session

    try:
        response = await client.post(
            f"/api/v1/telegram/webhook/{bot_id}",
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret-token"},
            json={"update_id": 1, "message": {"message_id": 10}},
        )
        assert response.status_code == 403
        assert "Invalid webhook secret" in response.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.unit
async def test_webhook_enforces_rate_limiting(client: AsyncClient, app: Any) -> None:
    """Verify exceeding rate limit triggers 429 Too Many Requests."""
    bot_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    user_id = 999999

    mock_bot = Bot(
        id=bot_id,
        tenant_id=tenant_id,
        bot_username="test_bot",
        token_fingerprint="abcdef1234567890",
        webhook_secret_ciphertext="secret",
        is_active=True,
    )

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = mock_bot
    mock_session.execute.return_value = mock_result

    app.dependency_overrides[get_db_session] = lambda: mock_session

    # Fill up the rate limiter for this user
    rate_limiter.reset()
    for _ in range(10):
        rate_limiter.is_allowed(user_id)

    try:
        response = await client.post(
            f"/api/v1/telegram/webhook/{bot_id}",
            headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
            json={
                "update_id": 1,
                "message": {
                    "message_id": 100,
                    "from": {"id": user_id, "first_name": "Flooder"},
                    "chat": {"id": user_id},
                    "text": "Flood message",
                },
            },
        )
        assert response.status_code == 429
        assert "Too many requests" in response.json()["detail"]
    finally:
        rate_limiter.reset()
        app.dependency_overrides.pop(get_db_session, None)
