"""Creates the configured LLM provider from application settings.

Configuration environment variables (docs/DEVELOPMENT_PLAN.md Stage 4):
- `LLM_PROVIDER`       — `openai` (OpenAI-compatible) or `mock` (default);
- `LLM_API_KEY`        — must never be committed, only env/secret storage;
- `LLM_API_BASE_URL`   — endpoint base URL (required for `openai`; this is
  how a local llama-server or a chosen cloud provider is connected);
- `LLM_DEFAULT_MODEL`  — model name (per-tenant override arrives later via
  `agents.model`);
- `LLM_TIMEOUT`, `LLM_MAX_RETRIES` — connection robustness knobs.

The concrete cloud vendor is deliberately not chosen (SEC-OD-1); any
OpenAI-compatible service is connected through `LLM_PROVIDER=openai`.
"""

from __future__ import annotations

from src.config.settings import Settings
from src.llm_providers.base import LLMProvider
from src.llm_providers.exceptions import LLMConfigurationError
from src.llm_providers.mock import MockLLMProvider
from src.llm_providers.openai_like import OpenAICompatibleProvider
from src.llm_providers.privacy import PrivacyFilter


class LLMProviderFactory:
    """Builds the provider instance selected by configuration."""

    @staticmethod
    def from_settings(
        settings: Settings, privacy_filter: PrivacyFilter | None = None
    ) -> LLMProvider:
        """Return the provider configured for the platform.

        Falls back to the mock provider when `LLM_PROVIDER` is unset — a
        safe offline default for development and tests.
        """
        provider = settings.llm_provider or "mock"

        if provider == "mock":
            return MockLLMProvider()

        if provider == "openai":
            if not settings.llm_api_key:
                raise LLMConfigurationError(
                    "LLM_PROVIDER is 'openai' but LLM_API_KEY is not set. "
                    "Provide the key via environment variables only."
                )
            if not settings.llm_api_base_url:
                raise LLMConfigurationError(
                    "LLM_PROVIDER is 'openai' but LLM_API_BASE_URL is not set. "
                    "Baked-in endpoints are not allowed; configure the base URL explicitly."
                )
            return OpenAICompatibleProvider(
                base_url=settings.llm_api_base_url,
                api_key=settings.llm_api_key,
                default_model=settings.llm_default_model,
                timeout=settings.llm_timeout,
                max_retries=settings.llm_max_retries,
                privacy_filter=privacy_filter,
            )

        raise LLMConfigurationError(
            f"Unknown LLM_PROVIDER '{provider}'. Supported values: 'openai', 'mock'."
        )
