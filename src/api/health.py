"""Health check endpoint for monitoring."""

from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    """Health check response schema."""

    status: str
    environment: str
    version: str = "0.1.0"


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Health check",
    description="Returns service availability status. DB check added in Phase 2.",
)
async def health_check() -> dict[str, Any]:
    """Return service health status."""
    from src.config.settings import get_settings

    settings = get_settings()
    return {
        "status": "ok",
        "environment": settings.app_env,
        "version": "0.1.0",
    }
