"""Unit tests for structured logging and secrets redacting."""

import logging

import pytest

from src.monitoring.logging import censor_secrets


@pytest.mark.unit
def test_censor_secrets_redacts_sensitive_keys() -> None:
    """Verify secret and token fields are replaced with [REDACTED]."""
    event_dict = {
        "event": "user login",
        "api_key": "sk-1234567890abcdef",
        "token": "bot123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        "webhook_secret": "my-secret-value",
        "password": "super-secret-password",
        "phone": "+79991234567",
        "user_id": 42,
        "status": "success",
    }

    censored = censor_secrets(logging.getLogger("test"), "info", event_dict)

    assert censored["api_key"] == "[REDACTED]"
    assert censored["token"] == "[REDACTED]"
    assert censored["webhook_secret"] == "[REDACTED]"
    assert censored["password"] == "[REDACTED]"
    assert censored["phone"] == "[REDACTED]"
    # Non-sensitive keys must remain intact
    assert censored["user_id"] == 42
    assert censored["status"] == "success"
    assert censored["event"] == "user login"
