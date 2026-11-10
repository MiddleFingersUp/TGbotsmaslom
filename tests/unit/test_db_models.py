"""Unit tests for SQLAlchemy 2 database models and schemas."""

import uuid

import pytest

from src.db.base import Base
from src.db.models import (
    BookingStatus,
    ConversationState,
    HandoffStatus,
    HandoffTrigger,
    LeadSource,
    LeadStatus,
    MessageRole,
    SecretSource,
    Service,
    ServiceStatus,
    Tenant,
    TenantStatus,
)

pytestmark = [pytest.mark.database, pytest.mark.unit]


@pytest.mark.unit
def test_all_13_tables_registered_in_metadata() -> None:
    """Verify all 13 core and helper tables defined in DATABASE.md are present in metadata."""
    expected_tables = {
        "tenants",
        "bots",
        "agents",
        "services",
        "customers",
        "conversations",
        "messages",
        "leads",
        "bookings",
        "knowledge_documents",
        "handoffs",
        "faqs",
        "token_usage",
    }
    registered_tables = set(Base.metadata.tables.keys())
    assert expected_tables.issubset(registered_tables)


@pytest.mark.unit
def test_customer_facing_tables_contain_tenant_id() -> None:
    """Verify multi-tenant isolation invariant: every customer-related table has tenant_id."""
    customer_tables = [
        "bots",
        "agents",
        "services",
        "customers",
        "conversations",
        "messages",
        "leads",
        "bookings",
        "knowledge_documents",
        "handoffs",
        "faqs",
        "token_usage",
    ]
    for table_name in customer_tables:
        table = Base.metadata.tables[table_name]
        assert "tenant_id" in table.c, f"Table {table_name} missing mandatory tenant_id column"
        assert not table.c["tenant_id"].nullable, f"tenant_id in {table_name} must be NOT NULL"


@pytest.mark.unit
def test_enum_members_match_specification() -> None:
    """Verify PostgreSQL ENUM types defined in DATABASE.md §3."""
    assert TenantStatus.ACTIVE.value == "active"
    assert ServiceStatus.ARCHIVED.value == "archived"
    assert ConversationState.NEED_DISCOVERY.value == "need_discovery"
    assert MessageRole.AGENT.value == "agent"
    assert LeadStatus.WON.value == "won"
    assert LeadSource.AGENT.value == "agent"
    assert BookingStatus.REQUESTED.value == "requested"
    assert HandoffTrigger.CLIENT_REQUEST.value == "client_request"
    assert HandoffStatus.RETURNED.value == "returned"
    assert SecretSource.CIPHER.value == "cipher"


@pytest.mark.unit
def test_model_instantiation() -> None:
    """Verify entity models can be instantiated with valid defaults and columns."""
    tenant_id = uuid.uuid4()
    tenant = Tenant(name="Test Salon", default_language="ru", status=TenantStatus.ACTIVE)
    assert tenant.default_language == "ru"
    assert tenant.status == TenantStatus.ACTIVE

    service = Service(
        tenant_id=tenant_id,
        name="Haircut",
        description="Standard haircut",
        currency="RUB",
        status=ServiceStatus.ACTIVE,
    )
    assert service.currency == "RUB"
    assert service.status == ServiceStatus.ACTIVE
