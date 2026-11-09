"""Unit tests for LLMProviderFactory and environment-driven provider selection."""

from __future__ import annotations

import pytest

from src.config.settings import Settings
from src.llm_providers.exceptions import LLMConfigurationError
from src.llm_providers.factory import LLMProviderFactory
from src.llm_providers.mock import MockLLMProvider
from src.llm_providers.openai_like import OpenAICompatibleProvider


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    """Build an isolated Settings from explicit env overrides."""
    for name in list(__import__("os").environ):
        if name.startswith("LLM_"):
            monkeypatch.delenv(name, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()


@pytest.mark.unit
class TestLLMProviderFactory:
    """Provider selection follows configuration only; no baked-in vendors."""

    def test_unset_provider_defaults_to_mock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = LLMProviderFactory.from_settings(_settings(monkeypatch))
        assert isinstance(provider, MockLLMProvider)

    def test_explicit_mock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = LLMProviderFactory.from_settings(_settings(monkeypatch, LLM_PROVIDER="mock"))
        assert isinstance(provider, MockLLMProvider)

    def test_openai_provider_created_with_full_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = LLMProviderFactory.from_settings(
            _settings(
                monkeypatch,
                LLM_PROVIDER="openai",
                LLM_API_KEY="sk-test-offline",
                LLM_API_BASE_URL="http://127.0.0.1:1234/v1",
                LLM_DEFAULT_MODEL="qwen3.5-9b",
                LLM_TIMEOUT="12",
                LLM_MAX_RETRIES="1",
            )
        )
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.provider_name == "openai_compatible"

    def test_openai_without_key_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(LLMConfigurationError):
            LLMProviderFactory.from_settings(
                _settings(
                    monkeypatch, LLM_PROVIDER="openai", LLM_API_BASE_URL="http://127.0.0.1:1234/v1"
                )
            )

    def test_openai_without_base_url_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Baking the endpoint into code is forbidden; base URL is required."""
        with pytest.raises(LLMConfigurationError):
            LLMProviderFactory.from_settings(
                _settings(monkeypatch, LLM_PROVIDER="openai", LLM_API_KEY="sk-test-offline")
            )

    def test_unknown_provider_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(monkeypatch)
        object.__setattr__(
            settings, "llm_provider", "azure"
        )  # invalid Literal → simulate misconfiguration
        with pytest.raises(LLMConfigurationError):
            LLMProviderFactory.from_settings(settings)
