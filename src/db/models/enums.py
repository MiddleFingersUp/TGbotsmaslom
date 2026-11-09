"""PostgreSQL ENUM types definition matching DATABASE.md §3."""

import enum


class TenantStatus(enum.StrEnum):
    """Tenant account status."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"


class ServiceStatus(enum.StrEnum):
    """Service catalog item status."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


class ConversationState(enum.StrEnum):
    """Finite-state machine dialog states."""

    GREETING = "greeting"
    NEED_DISCOVERY = "need_discovery"
    CONSULTATION = "consultation"
    OBJECTION_HANDLING = "objection_handling"
    LEAD_COLLECTION = "lead_collection"
    HANDOFF = "handoff"
    CLOSED = "closed"


class MessageRole(enum.StrEnum):
    """Message sender role."""

    USER = "user"
    AGENT = "agent"
    STAFF = "staff"
    SYSTEM = "system"


class LeadStatus(enum.StrEnum):
    """Lead lifecycle status."""

    NEW = "new"
    IN_PROGRESS = "in_progress"
    CONTACTED = "contacted"
    WON = "won"
    LOST = "lost"


class LeadSource(enum.StrEnum):
    """Lead origin source."""

    AGENT = "agent"
    STAFF = "staff"
    OPERATOR = "operator"


class BookingStatus(enum.StrEnum):
    """Booking verification status."""

    REQUESTED = "requested"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class HandoffTrigger(enum.StrEnum):
    """Reason triggering human handoff."""

    CLIENT_REQUEST = "client_request"
    AGENT_DECISION = "agent_decision"
    SYSTEM_ERROR = "system_error"


class HandoffStatus(enum.StrEnum):
    """Human handoff lifecycle status."""

    ACTIVE = "active"
    RETURNED = "returned"
    CLOSED = "closed"


class DocumentStatus(enum.StrEnum):
    """Knowledge base document publication status."""

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class SecretSource(enum.StrEnum):
    """Storage mechanism for bot credentials."""

    ENV = "env"
    CIPHER = "cipher"
    VAULT = "vault"
