"""Application configuration management via pydantic-settings."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Security
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
