"""API routing package."""

from fastapi import APIRouter

from src.api.health import router as health_router
from src.api.webhook import router as webhook_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["System"])
api_router.include_router(webhook_router, tags=["Telegram"])

__all__ = ["api_router"]
