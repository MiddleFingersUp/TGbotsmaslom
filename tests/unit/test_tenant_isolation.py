"""Unit tests for TenantContext and TenantRepository isolation enforcement."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.db.context import TenantContext
from src.db.models.customers import Customer
from src.db.models.services import Service
from src.db.repositories.base import TenantRepository


@pytest.mark.unit
def test_tenant_context_requires_valid_uuid() -> None:
    """Verify TenantContext validation prevents empty tenant IDs."""
    tenant_id = uuid.uuid4()
    context = TenantContext(tenant_id=tenant_id)
    assert context.tenant_id == tenant_id

    with pytest.raises(ValueError, match="non-empty tenant_id"):
        # Zero UUID or empty representation
        TenantContext(tenant_id=uuid.UUID(int=0))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tenant_repository_assigns_tenant_id_on_add() -> None:
    """Verify adding an entity automatically binds tenant_id from context."""
    tenant_id = uuid.uuid4()
    context = TenantContext(tenant_id=tenant_id)
    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    repo = TenantRepository(Service, mock_session, context)
    service = Service(name="Coloring", description="Hair coloring")

    await repo.add(service)

    assert service.tenant_id == tenant_id
    mock_session.add.assert_called_once_with(service)
    mock_session.flush.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tenant_repository_get_by_id_includes_tenant_filter() -> None:
    """Verify get_by_id injects tenant_id filter into query execution."""
    tenant_id = uuid.uuid4()
    context = TenantContext(tenant_id=tenant_id)
    entity_id = uuid.uuid4()

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = None
    mock_session.execute.return_value = mock_result

    repo = TenantRepository(Customer, mock_session, context)
    result = await repo.get_by_id(entity_id)

    assert result is None
    mock_session.execute.assert_awaited_once()

    # Verify query criteria
    query = mock_session.execute.call_args[0][0]
    compiled_sql = str(query.compile())
    assert "customers.tenant_id" in compiled_sql
    assert "customers.id" in compiled_sql
