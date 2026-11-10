"""Pytest fixtures for unit and integration testing."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.main import create_app

CATEGORY_MARKERS = ("unit", "integration", "agent", "security", "database")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Baseline for category tagging (Stage 8.1): an item without any of the
    known category markers is treated as a unit test (docs/DEVELOPMENT_PLAN.md
    §8 test categories). Explicit agent/security/database/integration markers
    are preserved as-is."""
    for item in items:
        if not any(item.get_closest_marker(name) for name in CATEGORY_MARKERS):
            item.add_marker(pytest.mark.unit)


@pytest.fixture
def app() -> FastAPI:
    """Fixture providing FastAPI application instance."""
    return create_app()


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Fixture providing async HTTP test client."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
