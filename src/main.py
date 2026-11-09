"""Main application factory and entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api import api_router
from src.config.settings import get_settings
from src.monitoring.logging import get_logger, setup_logging

logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan context manager for startup and shutdown events."""
    settings = get_settings()
    setup_logging(
        log_level=settings.log_level,
        json_logs=(settings.app_env != "dev"),
    )
    logger.info("Application starting", env=settings.app_env)
    yield
    logger.info("Application shutdown completed")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    app = FastAPI(
        title="TGbots AI Platform",
        description="Modular Monolith platform for Telegram AI Sales & Support agents",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(api_router)

    return app


app = create_app()
