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
    llm_api_key: str | None = Field(
        default=None,
        alias="LLM_API_KEY",
        description="API key for the cloud LLM provider",
    )
    llm_api_base_url: str | None = Field(
        default=None,
        alias="LLM_API_BASE_URL",
        description="Optional custom base URL for OpenAI-compatible endpoint",
    )
    llm_default_model: str = Field(
        default="gpt-4o-mini",
        alias="LLM_DEFAULT_MODEL",
        description="Platform default LLM model name",
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
