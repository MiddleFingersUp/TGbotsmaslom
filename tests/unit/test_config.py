"""Unit tests for configuration loading and validation."""

import pytest

from src.config.settings import Settings


@pytest.mark.unit
def test_default_settings() -> None:
    """Verify default settings instantiation (no baked-in LLM endpoint/model)."""
    settings = Settings()
    assert settings.app_env in {"dev", "test", "prod"}
    assert settings.log_level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    # Per SECURITY.md SEC-OD-1: model name is not baked into code.
    assert settings.llm_default_model is None
    assert settings.llm_provider is None  # unset → factory falls back to mock
    assert settings.llm_timeout == 30.0
    assert settings.llm_max_retries == 2
    assert settings.llm_temperature == 0.5
    assert settings.llm_max_tokens is None
    assert "postgresql" in settings.database_url


@pytest.mark.unit
def test_settings_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify environment variable overrides."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("LLM_DEFAULT_MODEL", "qwen3.5-9b")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_BASE_URL", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv("LLM_TIMEOUT", "45")
    monkeypatch.setenv("LLM_MAX_RETRIES", "3")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.1")
    monkeypatch.setenv("LLM_MAX_TOKENS", "512")

    settings = Settings()
    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"
    assert settings.llm_default_model == "qwen3.5-9b"
    assert settings.llm_provider == "openai"
    assert settings.llm_api_base_url == "http://127.0.0.1:1234/v1"
    assert settings.llm_timeout == 45.0
    assert settings.llm_max_retries == 3
    assert settings.llm_temperature == 0.1
    assert settings.llm_max_tokens == 512


@pytest.mark.unit
def test_llm_timeout_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Out-of-range LLM_TIMEOUT / LLM_MAX_RETRIES must fail validation."""
    monkeypatch.setenv("LLM_TIMEOUT", "300")
    with pytest.raises(ValueError):
        Settings()

    monkeypatch.delenv("LLM_TIMEOUT")
    monkeypatch.setenv("LLM_MAX_RETRIES", "-1")
    with pytest.raises(ValueError):
        Settings()
