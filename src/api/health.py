"""Health check endpoint for monitoring."""

from typing import Any

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from src.config.settings import get_settings
from src.db.session import get_engine
from src.monitoring.logging import get_logger

router = APIRouter()
logger = get_logger("health")


class HealthResponse(BaseModel):
    """Health check response schema."""

    status: str
    environment: str
    version: str = "0.1.0"
    database: str


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Health check",
    description="Returns service and database availability status. Returns 503 if DB check fails.",
)
async def health_check() -> Any:
    """Return service and database health status."""
    settings = get_settings()

    db_status = "disconnected"
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as exc:
        logger.warning("Database health check failed", error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "degraded",
                "environment": settings.app_env,
                "version": "0.1.0",
                "database": "disconnected",
            },
        )

    return {
        "status": "ok",
        "environment": settings.app_env,
        "version": "0.1.0",
        "database": db_status,
    }
