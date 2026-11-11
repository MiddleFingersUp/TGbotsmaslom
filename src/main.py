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


def run_cli() -> None:
    """CLI entrypoint (Stage 8.3): mode is an ARGUMENT, not an env var.

    - ``python -m src.main`` or ``python -m src.main webhook`` — HTTP API +
      Telegram webhook (default, unchanged);
    - ``python -m src.main poll`` — Telegram polling loop (pilot without a
      domain/TLS; see scripts/deploy_runbook.md).
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.main",
        description="TGbots platform entrypoint (webhook HTTP API or Telegram polling).",
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="webhook",
        choices=["webhook", "poll"],
        help="entrypoint mode: webhook (default, uvicorn HTTP API) or poll (getUpdates loop)",
    )
    args = parser.parse_args()

    if args.mode == "poll":
        import asyncio

        from src.telegram_adapter.polling import run_polling

        try:
            exit_code = asyncio.run(run_polling())
        except KeyboardInterrupt:
            exit_code = 0  # graceful Ctrl+C (Windows CLI path)
        raise SystemExit(exit_code)

    import uvicorn

    uvicorn.run("src.main:create_app", factory=True, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    run_cli()
