"""Unit tests for the application-layer TokenUsageService.

Verifies that usage statistics reach the database through the existing
TokenUsageRepository / token_usage model (Stage 2), and that incomplete or
absent usage is never persisted nor fabricated (docs/DEVELOPMENT_PLAN.md §10).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.llm_usage_service import TokenUsageService
from src.db.context import TenantContext
from src.db.models.token_usage import TokenUsage as TokenUsageRow
from src.llm_providers.schemas import LLMResponse, TokenUsage


def _tenant() -> TenantContext:
    return TenantContext(tenant_id=uuid.uuid4())


def _response(usage: TokenUsage) -> LLMResponse:
    return LLMResponse(
        content="Ответ",
        model="gpt-test",
        provider="openai_compatible",
        usage=usage,
    )


def _mock_session() -> AsyncMock:
    session = AsyncMock()
    session.commit = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.mark.unit
@pytest.mark.asyncio
class TestTokenUsageService:
    """Accounting honesty: only provider-reported data is written."""

    async def test_records_complete_usage(self) -> None:
        session = _mock_session()
        service = TokenUsageService(session)
        tenant = _tenant()
        response = _response(TokenUsage(prompt_tokens=11, completion_tokens=7, total_tokens=18))

        row = await service.record(tenant, response, conversation_id=uuid.uuid4())

        assert isinstance(row, TokenUsageRow)
        assert row.prompt_tokens == 11
        assert row.completion_tokens == 7
        assert row.provider == "openai_compatible"
        assert row.model == "gpt-test"
        assert row.tenant_id == tenant.tenant_id
        session.add.assert_called_once()
        # No commit: transaction ownership belongs to the calling use case.
        session.commit.assert_not_awaited()

    async def test_absent_usage_not_persisted(self) -> None:
        session = _mock_session()
        service = TokenUsageService(session)
        response = _response(TokenUsage())  # provider returned nothing

        row = await service.record(_tenant(), response)

        assert row is None
        session.add.assert_not_called()

    async def test_incomplete_usage_not_persisted(self) -> None:
        """Only `total_tokens` reported → we do not distribute/guess counters."""
        session = _mock_session()
        service = TokenUsageService(session)
        response = _response(TokenUsage(total_tokens=42))

        row = await service.record(_tenant(), response)

        assert row is None
        session.add.assert_not_called()

    async def test_tenant_isolation_via_context(self) -> None:
        """Rows are always bound to the explicitly passed tenant context."""
        session = _mock_session()
        service = TokenUsageService(session)
        tenant = _tenant()
        response = _response(TokenUsage(prompt_tokens=1, completion_tokens=1))

        row = await service.record(tenant, response)

        assert row is not None
        assert row.tenant_id == tenant.tenant_id
