"""Unit tests for configuration loading and validation."""

import pytest

from src.config.settings import Settings


@pytest.mark.unit
def test_default_settings() -> None:
    """Verify default settings instantiation."""
    settings = Settings()
    assert settings.app_env in {"dev", "test", "prod"}
    assert settings.log_level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    assert settings.llm_default_model == "gpt-4o-mini"
    assert "postgresql" in settings.database_url


@pytest.mark.unit
def test_settings_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify environment variable overrides."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("LLM_DEFAULT_MODEL", "gpt-4o")

    settings = Settings()
    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"
    assert settings.llm_default_model == "gpt-4o"
