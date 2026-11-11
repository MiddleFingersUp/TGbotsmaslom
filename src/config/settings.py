"""Application configuration management via pydantic-settings."""

from functools import lru_cache
from typing import TYPE_CHECKING, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from aiogram import Bot

from src.monitoring.logging import get_logger

logger = get_logger("config.settings")


class Settings(BaseSettings):
    """Platform settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    app_env: Literal["dev", "test", "prod"] = Field(
        default="dev",
        alias="APP_ENV",
        description="Current runtime environment",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        alias="LOG_LEVEL",
        description="Structured logging level",
    )

    # PostgreSQL
    database_url: str = Field(
        default="postgresql+asyncpg://tgbots:tgbots_dev@localhost:5432/tgbots",
        alias="DATABASE_URL",
        description="Async SQLAlchemy database connection string",
    )

    # LLM Settings
    llm_provider: Literal["openai", "mock"] | None = Field(
        default=None,
        alias="LLM_PROVIDER",
        description=(
            "LLM provider kind: 'openai' (OpenAI-compatible endpoint) or "
            "'mock' (offline test provider). Unset = mock (safe offline default)."
        ),
    )
    llm_api_key: str | None = Field(
        default=None,
        alias="LLM_API_KEY",
        description="API key for the OpenAI-compatible LLM endpoint (secret, never committed)",
    )
    llm_api_base_url: str | None = Field(
        default=None,
        alias="LLM_API_BASE_URL",
        description=(
            "Base URL of the OpenAI-compatible endpoint "
            "(e.g. a local llama-server for development or the chosen cloud provider)"
        ),
    )
    llm_default_model: str | None = Field(
        default=None,
        alias="LLM_DEFAULT_MODEL",
        description=(
            "Platform default LLM model name; must be configured explicitly (no baked-in model)"
        ),
    )
    llm_timeout: float = Field(
        default=30.0,
        alias="LLM_TIMEOUT",
        description="Default timeout in seconds for a single LLM request",
        ge=1.0,
        le=120.0,
    )
    llm_max_retries: int = Field(
        default=2,
        alias="LLM_MAX_RETRIES",
        description="Maximum automatic retries for transient LLM errors",
        ge=0,
        le=5,
    )
    llm_temperature: float = Field(
        default=0.5,
        alias="LLM_TEMPERATURE",
        description="Default sampling temperature applied when building agent requests",
        ge=0.0,
        le=2.0,
    )
    llm_max_tokens: int | None = Field(
        default=None,
        alias="LLM_MAX_TOKENS",
        description="Optional default completion token budget for LLM requests",
        ge=1,
    )

    # Telegram (single-bot MVP, Stage 7.2)
    telegram_bot_token: str | None = Field(
        default=None,
        alias="TELEGRAM_BOT_TOKEN",
        description="Bot token for staff relays/deliveries (secret: env only, never logged)",
    )

    # Security
    # NFR-1/NFR-2 (docs/SECURITY.md §10, §19): webhook rate limiting is
    # configurable so prod operators can tune protection without code changes.
    # Defaults (10 requests / 60 seconds per user) are EXACTLY the previous
    # hard-coded values — no security level change (Stage 8.2).
    webhook_rate_limit_max: int = Field(
        default=10,
        alias="WEBHOOK_RATE_LIMIT_MAX",
        description=(
            "Max webhook requests per user inside the sliding window (NFR-1); "
            "exceeding it returns 429"
        ),
        ge=1,
        le=10_000,
    )
    webhook_rate_limit_window: int = Field(
        default=60,
        alias="WEBHOOK_RATE_LIMIT_WINDOW",
        description="Sliding-window size in seconds for the webhook rate limiter (NFR-2)",
        ge=1,
        le=86_400,
    )

    webhook_secret: str | None = Field(
        default=None,
        alias="WEBHOOK_SECRET",
        description="Secret token for Telegram webhook validation",
    )
    admin_api_token: str | None = Field(
        default=None,
        alias="ADMIN_API_TOKEN",
        description="Operator bearer token for administrative/monitoring endpoints",
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton instance of Settings."""
    return Settings()


def build_default_bot() -> "Bot | None":
    """Build the Caller-Bot from platform settings (Stage 7.2).

    Fail-closed rule: without `TELEGRAM_BOT_TOKEN` there is NO Bot instance —
    every delivery path checks it and honеstly reports a skip instead of
    sending with a broken client. The token value is never logged here or
    anywhere downstream (docs/SECURITY.md §3).
    """
    from aiogram import Bot

    token = get_settings().telegram_bot_token
    if not token or not token.strip():
        return None
    try:
        return Bot(token=token.strip())
    except Exception as error:  # noqa: BLE001 - invalid-token configs fail closed
        logger.warning(
            "config.bot_builder_failed",
            error_type=type(error).__name__,
        )
        return None
