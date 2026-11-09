"""Tenant context value object for multi-tenant isolation."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class TenantContext:
    """Explicit tenant context passed to tenant-aware repositories and services.

    Guarantees that tenant_id is never None and enforces isolation.
    """

    tenant_id: UUID

    def __post_init__(self) -> None:
        """Validate that tenant_id is not empty or nil."""
        if not self.tenant_id or self.tenant_id == UUID(int=0):
            raise ValueError("TenantContext requires a non-empty tenant_id UUID.")
