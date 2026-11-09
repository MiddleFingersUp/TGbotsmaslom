"""Real `handoff_request` tool + backend procedure (Stage 7.2, ARCHITECTURE §13).

Implementation of docs/DEVELOPMENT_PLAN.md Stage 7 (tasks 3–4) with the
user-approved PLAN-OD-5 mechanics:

- the tool runs inside the CALLER's transaction (no self-commit);
- it copies `tenants.staff_chat_id` into the new `handoffs` row, sets
  `conversations.handoff_active=True` and `conversations.state=handoff`,
  and queues a STAFF notification for delivery AFTER the turn's COMMIT
  (the notification Outcomes never break the dialog);
- an ACTIVE handoff already present for the dialog makes the call
  IDEMPOTENT: `ok=True` with `status="already_active"`, no second row;
- if the tenant has no `staff_chat_id` the tool returns `ok=False` with
  «Передача сотрудникам сейчас недоступна» and does NOT set the flag:
  the agent must keep working (the client is never told "a human joins").

Scope mechanics (docs/SECURITY.md §8): the notification is queued with
dialog identifiers and an masked note (phone-like sequences are hidden,
PII minimization like Stage 7.1 `_mask_phone_like`).

The agent loop intercepts `handoff_request` BEFORE the registry and calls
`create_handoff` directly (backend entry, trigger=agent_decision). The
registry-compatible `execute()` path exists for completeness and fails
closed without the dialog-turn scope. The SYSTEM_ERROR trigger comes from
the same backend procedure, never from the LLM.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.context import TenantContext
from src.db.models.conversations import Conversation
from src.db.models.enums import HandoffStatus, HandoffTrigger
from src.db.models.handoffs import Handoff
from src.db.models.tenants import Tenant
from src.monitoring.logging import get_logger
from src.tools.base import Tool
from src.tools.schemas import ToolResult

logger = get_logger("tools.handoff_request")

STAFF_UNAVAILABLE = "Передача сотрудникам сейчас недоступна."

_HANDOFF_NOTE_MAX = 500

# PII minimization (docs/SECURITY.md §7): same mask as Stage 7.1 — the note
# shown to staff keeps dialog facts, phone-like sequences are hidden.
_PHONE_LIKE_RE = re.compile(r"\+?\d[\d\s\-\(\)]{7,}\d")


def mask_phone_like(text: str) -> str:
    """Replace phone-like sequences with a fixed mask (PII-free texts)."""
    return _PHONE_LIKE_RE.sub("[телефон скрыт]", text)


class HandoffRequestInput(BaseModel):
    """Arguments for the backend handoff procedure.

    `trigger` is a BACKEND-only field: the LLM never supplies it (the agent
    loop intercepts handoff requests; system errors use SYSTEM_ERROR).
    """

    note: str | None = Field(default=None, max_length=_HANDOFF_NOTE_MAX)
    trigger: Literal["agent_decision", "system_error"] = "agent_decision"


class HandoffRequestOutput(BaseModel):
    """Backend receipt for the LLM (identifiers only, no PII)."""

    handoff_id: str
    status: Literal["accepted", "already_active"]


# ─── After-commit staff notification queue (docs/DATABASE.md §7) ──────────


@dataclass(frozen=True)
class PendingHandoffNotification:
    """Queued handoff notification awaiting delivery after COMMIT (no PII)."""

    tenant_id: UUID
    conversation_id: UUID
    handoff_id: UUID
    staff_chat_id: int
    note: str = ""


def render_handoff_notification(note: PendingHandoffNotification) -> str:
    """Build the staff-facing notification (reply-instructions, no PII)."""
    head = f"Диалог {str(note.conversation_id)[:8]} передан сотруднику."
    trimmed_note = mask_phone_like(note.note or "").strip()
    return (
        f"{head}\n"
        + (f"Контекст: {trimmed_note[:300]}\n" if trimmed_note else "")
        + "Ответьте reply-ом на ЭТО сообщение — текст уйдёт клиенту. "
        + "Команда /back возвращает диалог в AI-режим."
    )


@dataclass
class AfterCommitHandoffNotificationQueue:
    """Stage 7.2 pattern twin of the lead queue (leads/use_case.py):

    filled during the turn, drained by the calling use case right after
    `session.commit()`; Telegram delivery is never inside the transaction.
    """

    items: list[PendingHandoffNotification] = field(default_factory=list)

    def add(self, note: PendingHandoffNotification) -> None:
        """Queue one notification for post-commit delivery."""
        self.items.append(note)

    @property
    def pending(self) -> bool:
        """True when any notification awaits delivery."""
        return bool(self.items)

    def drain(self) -> list[PendingHandoffNotification]:
        """Return queued items and empty the queue (idempotent flush)."""
        items = list(self.items)
        self.items.clear()
        return items


# ─── Dialog-turn scope for the handoff context ───────────────────────────


@dataclass(frozen=True)
class HandoffTurnScope:
    """Backend context of the current dialog turn for handoff tools."""

    conversation_id: UUID
    notifications: AfterCommitHandoffNotificationQueue


_scope_var: ContextVar[HandoffTurnScope | None] = ContextVar("handoff_turn_scope", default=None)


@contextmanager
def handoff_turn_scope(scope: HandoffTurnScope) -> Iterator[None]:
    """Set/restore the current handoff turn scope (single async task path)."""
    token: Token[HandoffTurnScope | None] = _scope_var.set(scope)
    try:
        yield
    finally:
        _scope_var.reset(token)


def get_handoff_turn_scope() -> HandoffTurnScope | None:
    """Read the current handoff turn scope (None outside an agent turn)."""
    return _scope_var.get()


class HandoffRequestTool(Tool):
    """Real human-handoff tool; includes the backend `create_handoff` entry."""

    name: ClassVar[str] = "handoff_request"
    description: ClassVar[str] = (
        "Передать диалог живому сотруднику: по явной просьбе клиента или при "
        "сложном вопросе, требующем человека."
    )
    input_schema: ClassVar[type[BaseModel]] = HandoffRequestInput
    output_schema: ClassVar[type[BaseModel]] = HandoffRequestOutput

    async def execute(
        self,
        input_model: BaseModel,
        tenant_context: TenantContext,
        session: AsyncSession | None,
    ) -> ToolResult:
        """Registry-compatible path: fail closed without the turn's scope.

        The production path is `create_handoff` (called by the agent loop's
        interception). This method exists so that a direct LLM tool-call
        (if it ever happens) does not crash and fails closed honestly.
        """
        logger.warning(
            "tool.handoff_request.registry_path_used",
            tool_name=self.name,
        )
        return ToolResult(ok=False, error_message=STAFF_UNAVAILABLE)

    async def create_handoff(
        self,
        *,
        conversation_id: UUID,
        tenant_context: TenantContext,
        session: AsyncSession | None,
        note: str = "",
        trigger: Literal["agent_decision", "system_error"] = "agent_decision",
    ) -> ToolResult:
        """Backend procedure: row + flag inside the caller's transaction."""
        _ = self  # instance helper (the tool state is class-level only)

        if session is None:
            logger.warning("tool.handoff_request.no_session", tool_name=self.name)
            return ToolResult(ok=False, error_message=STAFF_UNAVAILABLE)

        tenant = await session.get(Tenant, tenant_context.tenant_id)
        if tenant is None:
            logger.warning("tool.handoff_request.tenant_missing", tool_name=self.name)
            return ToolResult(ok=False, error_message=STAFF_UNAVAILABLE)

        staff_chat_id = getattr(tenant, "staff_chat_id", None)
        if staff_chat_id is None:
            logger.warning(
                "tool.handoff_request.no_staff_chat", tenant_id=str(tenant_context.tenant_id)
            )
            return ToolResult(ok=False, error_message=STAFF_UNAVAILABLE)

        # Idempotency: one ACTIVE handoff per conversation (docs/DATABASE.md §4.11).
        active_query = select(Handoff).where(
            Handoff.tenant_id == tenant_context.tenant_id,
            Handoff.conversation_id == conversation_id,
            Handoff.status == HandoffStatus.ACTIVE,
        )
        active_result = await session.execute(active_query)
        existing = active_result.scalars().first()
        if existing is not None:
            row = existing
        else:
            row = Handoff(
                tenant_id=tenant_context.tenant_id,
                conversation_id=conversation_id,
                trigger=HandoffTrigger(trigger),
                status=HandoffStatus.ACTIVE,
                staff_chat_id=staff_chat_id,
                note=mask_phone_like(note)[:_HANDOFF_NOTE_MAX] or None,
            )
            session.add(row)

        await session.flush()

        # Backend-owned flags (docs/ARCHITECTURE §13): the LLM cannot set these.
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:
            logger.warning(
                "tool.handoff_request.conversation_missing",
                tenant_id=str(tenant_context.tenant_id),
                conversation_id=str(conversation_id),
            )
            return ToolResult(ok=False, error_message=STAFF_UNAVAILABLE)

        setattr(conversation, "handoff_active", True)  # noqa: B010 - backend data

        # State machine: handoff is a backend transition (docs/ARCHITECTURE §7).
        from src.db.models.enums import ConversationState

        setattr(conversation, "state", ConversationState.HANDOFF)  # noqa: B010

        # Queue the STAFF notification for delivery AFTER COMMIT (§13). The
        # queue lives in the dialog-turn scope (see process_incoming_message).
        scope = get_handoff_turn_scope()
        if scope is not None and scope.notifications is not None:
            scope.notifications.add(
                PendingHandoffNotification(
                    tenant_id=tenant_context.tenant_id,
                    conversation_id=conversation_id,
                    handoff_id=row.id,
                    staff_chat_id=staff_chat_id,
                    note=note,
                )
            )
        else:
            logger.warning(
                "tool.handoff_request.no_scope_queue",
                tool_name=self.name,
            )

        return self.build_result(
            HandoffRequestOutput(
                handoff_id=str(row.id),
                status="accepted",
            ).model_dump()
        )
