"""Lead save use case + lifecycle + after-commit notification queue (Stage 7.1).

Implements docs/DEVELOPMENT_PLAN.md Stage 7 task 1 (minus handoff, which is
7.2) against docs/DATABASE.md §4.8 and §7 п. 2:

- input is validated through a Pydantic schema (`LeadInput`); the phone
  number is OPTIONAL, but at least one contact channel (phone or Telegram
  username) is mandatory — `ck_leads_contact_required` at the schema level;
- DEDUPLICATION (очередь «обновление, не дубликат»): a repeated lead in the
  SAME dialog updates the existing ACTIVE lead (`new`/`in_progress`) —
  name/contact/intent are refreshed, no second row is created;
- status changes go through `SaveLeadUseCase.update_status` with the
  lifecycle transition map (new → in_progress → contacted → won/lost);
  jump transitions are rejected;
- everything is tenant-isolated: queries run via the tenant-scoped session
  filters; `tenant_id` comes only from `TenantContext`;
- NO self-managed commit: the use case modifies the caller's transaction
  (the whole agent turn commits once in the calling use case);
- AFTER-COMMIT NOTIFICATIONS: during the turn the outcome is appended to
  `AfterCommitNotificationQueue`; the CALLING use case flushes the queue
  right after `session.commit()` (docs/DATABASE.md §7: Telegram delivery
  is never inside the transaction). Delivery uses `Notifier`; a missing
  `tenants.staff_chat_id` means SKIPPED + warn log, never an error for
  the dialog turn.

Conversation context (`conversation_id`) for the `save_lead` tool is
passed through a `ContextVar` scope (`lead_turn_scope`) set by the calling
use case around the agent loop — the tool-registry protocol stays
unchanged (no changes to `run_tool` / `Tool` contract).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.context import TenantContext
from src.db.models.conversations import Conversation
from src.db.models.enums import LeadSource, LeadStatus
from src.db.models.leads import Lead
from src.db.models.services import Service
from src.monitoring.logging import get_logger

logger = get_logger("leads.use_case")

_LEAD_UPDATE_COMMENT_MAX = 500
_CONTEXT_SUMMARY_MAX = 2000

# 152-ФЗ / PII minimization: the queued notification text keeps dialog
# identifiers and the request summary, but phone-like sequences (typed by
# the user inside the intent text) are masked — contacts live in the
# `leads` row and the Stage 7.2 staff view, never in notification payloads.
_PHONE_LIKE_RE = re.compile(r"\+?\d[\d\s\-\(\)]{7,}\d")


def _mask_phone_like(text: str) -> str:
    """Replace phone-like sequences with a fixed mask (PII-free texts)."""
    return _PHONE_LIKE_RE.sub("[телефон скрыт]", text)


class LeadStatusError(ValueError):
    """Rejected lifecycle transition (all messages are operator-safe)."""


# ─── Lifecycle (DATABASE.md §4.8, FR-5.3) ────────────────────────────────
# Sequential chain; jumps (e.g. new → won) are denied by design.
LEAD_STATUS_TRANSITIONS: dict[LeadStatus, frozenset[LeadStatus]] = {
    LeadStatus.NEW: frozenset({LeadStatus.IN_PROGRESS}),
    LeadStatus.IN_PROGRESS: frozenset({LeadStatus.CONTACTED}),
    LeadStatus.CONTACTED: frozenset({LeadStatus.WON, LeadStatus.LOST}),
    LeadStatus.WON: frozenset(),
    LeadStatus.LOST: frozenset(),
}


def can_transition(current: LeadStatus, target: LeadStatus) -> bool:
    """True when `current → target` is an allowed lifecycle transition."""
    return target in LEAD_STATUS_TRANSITIONS[current]


# ─── Input schema ────────────────────────────────────────────────────────


class LeadInput(BaseModel):
    """Validated lead fields for `SaveLeadUseCase.save` (PII-bounded lengths).

    Field → column mapping (the DB schema stays frozen, DEVELOPMENT_PLAN
    Stage 7): `summary` → `leads.intent`; `comment` → appended to the
    conversation `context_summary` (fact of the dialog, §7 п. 2).
    """

    contact_phone: str | None = Field(default=None, max_length=50)
    contact_username: str | None = Field(default=None, max_length=255)
    summary: str = Field(min_length=1, max_length=1000)
    comment: str | None = Field(default=None, max_length=_LEAD_UPDATE_COMMENT_MAX)
    service_id: UUID | None = None

    @model_validator(mode="after")
    def requires_contact(self) -> LeadInput:
        """A lead must carry at least one contact channel (FR-5.1)."""
        phone = (self.contact_phone or "").strip()
        username = (self.contact_username or "").strip()
        if not phone and not username:
            raise ValueError("At least one of contact_phone / contact_username is required.")
        self.contact_phone = phone or None
        self.contact_username = username or None
        return self


class SaveLeadOutcome(BaseModel):
    """Structured result of one save (identifiers/statuses only, no PII)."""

    lead_id: str
    conversation_id: str
    status: LeadStatus
    is_update: bool


# ─── After-commit notification queue (DATABASE.md §7 п. 2) ─────────────


@dataclass(frozen=True)
class PendingLeadNotification:
    """One queued lead notification awaiting delivery after COMMIT.

    No PII: identifiers and the trimmed intent only — the phone number is
    in the `leads` row and reaches the staff view in Stage 7.2, never the
    logs and never the queued text.
    """

    tenant_id: UUID
    conversation_id: UUID
    lead_id: UUID
    status: LeadStatus
    intent: str = ""
    is_update: bool = False


def render_lead_notification(note: PendingLeadNotification) -> str:
    """Build the staff-facing notification text (no contact PII)."""
    title = "Обновлена заявка" if note.is_update else "Новая заявка"
    intent = _mask_phone_like(note.intent or "")
    return f"{title} (status: {note.status.value}), диалог {str(note.conversation_id)[:8]}" + (
        f": суть — {intent[:200]}" if intent else ""
    )


@dataclass
class AfterCommitNotificationQueue:
    """Collects lead notifications during the turn; drained after COMMIT.

    Mechanism decision (iteration 7.1, proposed and accepted here): the
    queue is created per dialog turn and flushed by the calling use case
    IMMEDIATELY after its `await session.commit()`. A SQLAlchemy
    `after_commit` event listener was considered, but on async sessions it
    is a synchronous callback — async delivery would need fire-and-forget
    tasks and would lose deterministic ordering and failure scoping.
    """

    items: list[PendingLeadNotification] = field(default_factory=list)

    def add(self, note: PendingLeadNotification) -> None:
        """Queue one notification for post-commit delivery."""
        self.items.append(note)

    @property
    def pending(self) -> bool:
        """True when any notification awaits delivery."""
        return bool(self.items)

    def drain(self) -> list[PendingLeadNotification]:
        """Return queued items and empty the queue (idempotent flush)."""
        items = list(self.items)
        self.items.clear()
        return items


# ─── Dialog-turn scope for tools (replaces a registry protocol change) ──


@dataclass(frozen=True)
class LeadTurnScope:
    """Backend context of the current dialog turn for lead tools.

    Backend-owned data only: the LLM never supplies dialog identifiers.
    """

    conversation_id: UUID
    notifications: AfterCommitNotificationQueue


_scope_var: ContextVar[LeadTurnScope | None] = ContextVar("lead_turn_scope", default=None)


@contextmanager
def lead_turn_scope(scope: LeadTurnScope) -> Iterator[None]:
    """Set/restore the current dialog-turn scope (single async task path)."""
    token: Token[LeadTurnScope | None] = _scope_var.set(scope)
    try:
        yield
    finally:
        _scope_var.reset(token)


def get_lead_turn_scope() -> LeadTurnScope | None:
    """Read the current dialog-turn scope (None outside an agent turn)."""
    return _scope_var.get()


# ─── The use case ────────────────────────────────────────────────────────


class SaveLeadUseCase:
    """Creates or UPDATES the active lead of the dialog (no self-commit)."""

    def __init__(self, session: AsyncSession, tenant_context: TenantContext) -> None:
        """Operate strictly inside the caller's transaction and tenant."""
        self.session = session
        self.tenant_context = tenant_context
        self.tenant_id = tenant_context.tenant_id

    async def save(
        self,
        lead_input: LeadInput,
        *,
        conversation_id: UUID,
        notifications: AfterCommitNotificationQueue | None = None,
    ) -> SaveLeadOutcome:
        """Deduplicate by the dialog's active lead, then create or update.

        A repeat call in the same dialog refreshes the existing ACTIVE
        lead (`new`/`in_progress`) instead of creating a second row; a
        notification is queued for NEW leads only (an update does not
        re-notify staff — it is the same lead).
        """
        conversation = await self._get_conversation(conversation_id)
        if conversation is None:
            raise LookupError("conversation not found for the current tenant")

        lead = await self._get_active_lead(conversation.id)
        is_update = lead is not None

        if lead is None:
            lead = await self._create_lead(lead_input, conversation)
        else:
            self._update_lead(lead, lead_input)

        if not is_update and notifications is not None:
            notifications.add(
                PendingLeadNotification(
                    tenant_id=self.tenant_id,
                    conversation_id=conversation.id,
                    lead_id=lead.id,
                    status=lead.status,
                    intent=_mask_phone_like(lead_input.summary),
                    is_update=False,
                )
            )

        await self.session.flush()

        self._update_conversation_context(conversation, lead_input.comment)
        return SaveLeadOutcome(
            lead_id=str(lead.id),
            conversation_id=str(conversation.id),
            status=lead.status,
            is_update=is_update,
        )

    async def update_status(
        self,
        lead: Lead,
        target: LeadStatus,
    ) -> Lead:
        """Apply an allowed lifecycle transition; jumps raise `LeadStatusError`.

        Timestamps follow DATABASE.md §4.8: `contacted_at` on the first
        touch, `closed_at` on won/lost.
        """
        if not can_transition(lead.status, target):
            raise LeadStatusError(
                f"transition {lead.status.value} -> {target.value} is not allowed"
            )
        lead.status = target
        now_date = datetime.now(UTC)
        if target == LeadStatus.CONTACTED and lead.contacted_at is None:
            lead.contacted_at = now_date
        if target in (LeadStatus.WON, LeadStatus.LOST) and lead.closed_at is None:
            lead.closed_at = now_date
        await self.session.flush()
        return lead

    # ─── internals ─────────────────────────────────────────────────────

    async def _get_conversation(self, conversation_id: UUID) -> Conversation | None:
        """Fetch the dialog strictly within the tenant boundary."""
        query = select(Conversation).where(
            Conversation.tenant_id == self.tenant_id,
            Conversation.id == conversation_id,
        )
        result = await self.session.execute(query)
        return result.scalars().first()

    async def _get_active_lead(self, conversation_id: UUID) -> Lead | None:
        """The dialog's ACTIVE lead (`new`/`in_progress`) — singleton per dialog."""
        query = select(Lead).where(
            Lead.tenant_id == self.tenant_id,
            Lead.conversation_id == conversation_id,
            Lead.status.in_([LeadStatus.NEW, LeadStatus.IN_PROGRESS]),
        )
        result = await self.session.execute(query)
        return result.scalars().first()

    async def _create_lead(self, lead_input: LeadInput, conversation: Conversation) -> Lead:
        """Insert a fresh lead; tenant/customer ids come from backend data."""
        lead = Lead(
            tenant_id=self.tenant_id,
            conversation_id=conversation.id,
            customer_id=conversation.customer_id,
            service_id=lead_input.service_id,
            contact_phone=lead_input.contact_phone,
            contact_username=lead_input.contact_username,
            intent=lead_input.summary,
            status=LeadStatus.NEW,
            source=LeadSource.AGENT,
        )
        self.session.add(lead)
        return lead

    @staticmethod
    def _update_lead(lead: Lead, lead_input: LeadInput) -> None:
        """Refresh snapshot fields of the existing lead; status is untouched."""
        lead.contact_phone = lead_input.contact_phone or lead.contact_phone
        lead.contact_username = lead_input.contact_username or lead.contact_username
        lead.intent = lead_input.summary
        if lead_input.service_id is not None:
            lead.service_id = lead_input.service_id

    def _update_conversation_context(
        self,
        conversation: Conversation,
        comment: str | None,
    ) -> None:
        """Append the optional comment to the dialog facts (§7 п. 2)."""
        if not comment:
            return
        summary = conversation.context_summary or ""
        fact = comment[:_LEAD_UPDATE_COMMENT_MAX]
        conversation.context_summary = (
            f"{summary}\nlead: {fact}".strip()[:_CONTEXT_SUMMARY_MAX] or None
        )

    async def resolve_service_id(self, service_name: str | None) -> UUID | None:
        """Exact-name service lookup within the tenant (None → unresolved)."""
        if not service_name:
            return None
        query = select(Service.id).where(
            Service.tenant_id == self.tenant_id,
            Service.name == service_name,
        )
        result = await self.session.execute(query)
        return result.scalars().first()


__all__: list[str] = [
    "AfterCommitNotificationQueue",
    "LeadInput",
    "LeadStatusError",
    "LeadTurnScope",
    "PendingLeadNotification",
    "SaveLeadOutcome",
    "SaveLeadUseCase",
    "can_transition",
    "get_lead_turn_scope",
    "lead_turn_scope",
    "render_lead_notification",
]
