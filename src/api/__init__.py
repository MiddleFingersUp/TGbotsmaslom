"""API routing package."""

from fastapi import APIRouter

from src.api.health import router as health_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["System"])

__all__ = ["api_router"]
