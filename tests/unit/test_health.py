"""Unit tests for health endpoint."""

import pytest
from httpx import AsyncClient


@pytest.mark.unit
async def test_health_check_returns_200(client: AsyncClient) -> None:
    """Verify GET /health returns 200 OK with expected JSON structure."""
    response = await client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "ok"
    assert "environment" in data
    assert data["version"] == "0.1.0"
