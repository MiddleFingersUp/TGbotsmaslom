"""Unit tests for health endpoint with database check."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


@pytest.mark.unit
async def test_health_check_returns_200_when_db_connected(client: AsyncClient) -> None:
    """Verify GET /health returns 200 OK when database connection succeeds."""
    mock_conn = AsyncMock()
    mock_conn.execute.return_value = None

    mock_engine = MagicMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn

    with patch("src.api.health.get_engine", return_value=mock_engine):
        response = await client.get("/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "ok"
        assert data["database"] == "connected"
        assert data["version"] == "0.1.0"


@pytest.mark.unit
async def test_health_check_returns_503_when_db_fails(client: AsyncClient) -> None:
    """Verify GET /health returns 503 Service Unavailable when DB connection fails."""
    mock_engine = MagicMock()
    mock_engine.connect.side_effect = ConnectionRefusedError("Database connection refused")

    with patch("src.api.health.get_engine", return_value=mock_engine):
        response = await client.get("/health")
        assert response.status_code == 503

        data = response.json()
        assert data["status"] == "degraded"
        assert data["database"] == "disconnected"
