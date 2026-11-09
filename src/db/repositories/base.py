"""Generic base repository and tenant-scoped repository with mandatory tenant isolation."""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import Base
from src.db.context import TenantContext


class BaseRepository[ModelType: Base]:
    """Base repository providing generic CRUD operations on standalone entities (e.g. Tenant)."""

    def __init__(self, model: type[ModelType], session: AsyncSession) -> None:
        self.model = model
        self.session = session

    async def get_by_id(self, entity_id: uuid.UUID) -> ModelType | None:
        """Fetch single entity by primary key."""
        id_col = getattr(self.model, "id")  # noqa: B009
        result = await self.session.execute(select(self.model).where(id_col == entity_id))
        return result.scalars().first()

    async def list_all(self, limit: int = 100, offset: int = 0) -> Sequence[ModelType]:
        """List entities with pagination."""
        query = select(self.model).limit(limit).offset(offset)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def add(self, entity: ModelType) -> ModelType:
        """Stage entity for insertion."""
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def delete(self, entity_id: uuid.UUID) -> bool:
        """Delete entity by ID."""
        id_col = getattr(self.model, "id")  # noqa: B009
        result = await self.session.execute(delete(self.model).where(id_col == entity_id))
        rowcount = result.rowcount if isinstance(result, CursorResult) else 0
        return rowcount > 0


class TenantRepository[ModelType: Base]:
    """Tenant-isolated repository enforcing tenant_id filtering across ALL queries.

    Per DATABASE.md §9 and ARCHITECTURE.md §15:
    - Never accepts tenant_id = None
    - Always injects tenant_id into SELECT/UPDATE/DELETE/INSERT
    """

    def __init__(
        self,
        model: type[ModelType],
        session: AsyncSession,
        tenant_context: TenantContext,
    ) -> None:
        self.model = model
        self.session = session
        self.tenant_id = tenant_context.tenant_id

    async def get_by_id(self, entity_id: uuid.UUID) -> ModelType | None:
        """Fetch entity by ID strictly within tenant boundary."""
        id_col = getattr(self.model, "id")  # noqa: B009
        tenant_col = getattr(self.model, "tenant_id")  # noqa: B009
        query = select(self.model).where(
            id_col == entity_id,
            tenant_col == self.tenant_id,
        )
        result = await self.session.execute(query)
        return result.scalars().first()

    async def list_for_tenant(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[ModelType]:
        """List all entities belonging strictly to current tenant."""
        tenant_col = getattr(self.model, "tenant_id")  # noqa: B009
        query = select(self.model).where(tenant_col == self.tenant_id).limit(limit).offset(offset)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def add(self, entity: ModelType) -> ModelType:
        """Persist entity, explicitly assigning tenant_id from context."""
        setattr(entity, "tenant_id", self.tenant_id)  # noqa: B010
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def update_by_id(self, entity_id: uuid.UUID, values: dict[str, Any]) -> bool:
        """Update entity by ID strictly within tenant boundary."""
        values.pop("tenant_id", None)
        id_col = getattr(self.model, "id")  # noqa: B009
        tenant_col = getattr(self.model, "tenant_id")  # noqa: B009
        query = (
            update(self.model)
            .where(
                id_col == entity_id,
                tenant_col == self.tenant_id,
            )
            .values(**values)
        )
        result = await self.session.execute(query)
        rowcount = result.rowcount if isinstance(result, CursorResult) else 0
        return rowcount > 0

    async def delete_by_id(self, entity_id: uuid.UUID) -> bool:
        """Delete entity by ID strictly within tenant boundary."""
        id_col = getattr(self.model, "id")  # noqa: B009
        tenant_col = getattr(self.model, "tenant_id")  # noqa: B009
        query = delete(self.model).where(
            id_col == entity_id,
            tenant_col == self.tenant_id,
        )
        result = await self.session.execute(query)
        rowcount = result.rowcount if isinstance(result, CursorResult) else 0
        return rowcount > 0
