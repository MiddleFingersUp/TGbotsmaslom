"""Unit tests for Telegram Webhook endpoint security and request handling."""

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from src.api.webhook import configure_rate_limiter, get_rate_limiter, rate_limiter
from src.config.settings import Settings, get_settings
from src.db.models.bots import Bot
from src.db.session import get_db_session


def _mock_bot_session(bot_id: uuid.UUID, tenant_id: uuid.UUID, secret: str) -> Any:
    """Build a mock DB session returning an active bot with the given secret."""
    mock_bot = Bot(
        id=bot_id,
        tenant_id=tenant_id,
        bot_username="test_bot",
        token_fingerprint="abcdef1234567890",
        webhook_secret_ciphertext=secret,
        is_active=True,
    )
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = mock_bot
    mock_session.execute.return_value = mock_result
    return mock_session


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
@pytest.mark.security
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
@pytest.mark.security
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


# ─── Stage 8.2: rate limiter limits come from settings (NFR-1/NFR-2) ────


@pytest.mark.unit
def test_rate_limit_defaults_match_settings() -> None:
    """Defaults (10 req / 60 s) are equivalent to the previous hard-coded values."""
    settings = Settings()
    assert settings.webhook_rate_limit_max == 10
    assert settings.webhook_rate_limit_window == 60
    # Shared singleton is built from settings and returned by the getter.
    limiter = get_rate_limiter()
    assert limiter.max_requests == get_settings().webhook_rate_limit_max
    assert limiter.window_seconds == get_settings().webhook_rate_limit_window
    # Module-level alias and the getter must expose the SAME object so
    # in-place reconfiguration is visible to both.
    assert rate_limiter is limiter


@pytest.mark.unit
def test_rate_limit_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WEBHOOK_RATE_LIMIT_MAX / _WINDOW env vars override the limits (NFR-2)."""
    monkeypatch.setenv("WEBHOOK_RATE_LIMIT_MAX", "5")
    monkeypatch.setenv("WEBHOOK_RATE_LIMIT_WINDOW", "30")
    settings = Settings()
    assert settings.webhook_rate_limit_max == 5
    assert settings.webhook_rate_limit_window == 30


@pytest.mark.unit
def test_rate_limit_validation_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Out-of-range rate limit values must fail settings validation."""
    monkeypatch.setenv("WEBHOOK_RATE_LIMIT_MAX", "0")
    with pytest.raises(ValueError):
        Settings()

    monkeypatch.delenv("WEBHOOK_RATE_LIMIT_MAX")
    monkeypatch.setenv("WEBHOOK_RATE_LIMIT_WINDOW", "-1")
    with pytest.raises(ValueError):
        Settings()


@pytest.mark.unit
@pytest.mark.security
async def test_webhook_rate_limit_from_injected_settings(
    client: AsyncClient, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Endpoint honors an injected (settings-driven) limit; 429 semantics unchanged."""
    from types import SimpleNamespace

    bot_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    user_id = 888888

    # Allowed requests must pass through the dispatch path without touching
    # the DB: stub the use case (imported at call time inside the handler).
    class _StubUseCase:
        def __init__(self, session: Any) -> None:
            self.session = session

        async def execute(self, incoming: Any) -> Any:
            return SimpleNamespace(is_duplicate=False, response_text="ok")

    monkeypatch.setattr(
        "src.application.process_incoming_message.ProcessIncomingMessageUseCase",
        _StubUseCase,
    )
    app.dependency_overrides[get_db_session] = lambda: _mock_bot_session(
        bot_id, tenant_id, "secret"
    )
    # Inject a stricter limit than the default: 2 requests / 60 s.
    configure_rate_limiter(max_requests=2, window_seconds=60)

    payload = {
        "update_id": 1,
        "message": {
            "message_id": 100,
            "from": {"id": user_id, "first_name": "Flooder"},
            "chat": {"id": user_id},
            "text": "Flood message",
        },
    }
    headers = {"X-Telegram-Bot-Api-Secret-Token": "secret"}
    url = f"/api/v1/telegram/webhook/{bot_id}"

    try:
        first = await client.post(url, headers=headers, json=payload)
        assert first.status_code == 200
        second = await client.post(url, headers=headers, json=payload)
        assert second.status_code == 200
        # 3rd request exceeds max_requests=2 → 429, same detail as before.
        third = await client.post(url, headers=headers, json=payload)
        assert third.status_code == 429
        assert "Too many requests" in third.json()["detail"]
    finally:
        configure_rate_limiter()  # restore settings-based defaults + reset
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.unit
@pytest.mark.security
async def test_webhook_rate_limit_default_429_semantics_unchanged(
    client: AsyncClient, app: Any
) -> None:
    """With restored settings-based defaults, the 10th→429 behaviour holds."""
    bot_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    user_id = 777777

    app.dependency_overrides[get_db_session] = lambda: _mock_bot_session(
        bot_id, tenant_id, "secret"
    )
    # No-arg call re-applies get_settings() values — the pre-8.2 behaviour.
    configure_rate_limiter()
    limiter = get_rate_limiter()
    for _ in range(limiter.max_requests):
        limiter.is_allowed(user_id)

    payload = {
        "update_id": 1,
        "message": {
            "message_id": 100,
            "from": {"id": user_id, "first_name": "Flooder"},
            "chat": {"id": user_id},
            "text": "Flood message",
        },
    }
    headers = {"X-Telegram-Bot-Api-Secret-Token": "secret"}
    url = f"/api/v1/telegram/webhook/{bot_id}"

    try:
        response = await client.post(url, headers=headers, json=payload)
        assert response.status_code == 429
        assert "Too many requests" in response.json()["detail"]
    finally:
        configure_rate_limiter()  # reset counters for other tests
        app.dependency_overrides.pop(get_db_session, None)
