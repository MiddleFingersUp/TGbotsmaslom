"""Real `save_lead` tool backed by the Stage 7.1 lead use case.

Implementation of docs/DEVELOPMENT_PLAN.md Stage 7 task 1, in the style of
`src/tools/search_services.py`: the tool validates input, delegates to
`SaveLeadUseCase` on the SAME AsyncSession/transaction the agent loop
provides via `run_tool` (the tool opens NO new sessions and commits
NOTHING — the calling use case commits the whole turn once).

Dialog context: the caller (`ProcessIncomingMessageUseCase`) sets the
`lead_turn_scope` ContextVar around the agent turn with the backend-owned
conversation id and the after-commit notification queue; the LLM cannot
supply dialog identifiers and the registry protocol is unchanged.

Honesty/PII contract (docs/SECURITY.md §13, §8):
- `data` carries ONLY identifiers/statuses (lead id, status, conversation
  id, update flag) — contact phone/username never leave the tool's data;
- SQL/any transport failures degrade to `ToolResult(ok=False)` with a
  generic Russian message; raw error text is never returned to the model
  and never logged (fail-closed);
- without a session, a dialog scope, or a resolvable conversation the
  tool fails closed with the same generic message.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.context import TenantContext
from src.db.models.enums import LeadStatus
from src.leads.use_case import (
    LeadInput,
    SaveLeadUseCase,
    get_lead_turn_scope,
)
from src.monitoring.logging import get_logger
from src.tools.base import Tool
from src.tools.schemas import ToolResult

logger = get_logger("tools.save_lead")

_SAVE_LEAD_UNAVAILABLE = "Сохранить заявку сейчас не удаётся. Попробуйте ещё раз."


class SaveLeadRealInput(BaseModel):
    """Arguments for the real `save_lead` tool invocation."""

    intent: str = Field(min_length=1, max_length=1000)
    contact_phone: str | None = Field(default=None, max_length=50)
    contact_username: str | None = Field(default=None, max_length=255)
    service_name: str | None = Field(default=None, max_length=255)
    comment: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def requires_contact(self) -> SaveLeadRealInput:
        """A lead must carry at least one contact channel (FR-5.1)."""
        phone = (self.contact_phone or "").strip()
        username = (self.contact_username or "").strip()
        if not phone and not username:
            raise ValueError("At least one of contact_phone / contact_username is required.")
        self.contact_phone = phone or None
        self.contact_username = username or None
        return self


class SaveLeadRealOutput(BaseModel):
    """Tool output: identifiers/statuses only, contact PII stays out."""

    lead_id: str
    conversation_id: str
    status: Literal["new", "in_progress", "contacted", "won", "lost"]
    is_update: bool


class SaveLeadTool(Tool):
    """Real lead-saving tool (Stage 7.1); the stub stays for offline tests."""

    name: ClassVar[str] = "save_lead"
    description: ClassVar[str] = (
        "Сохранить заявку клиента. Вызывай только после того, как клиент "
        "сам сообщил телефон или дал согласие оставить заявку."
    )
    input_schema: ClassVar[type[BaseModel]] = SaveLeadRealInput
    output_schema: ClassVar[type[BaseModel]] = SaveLeadRealOutput

    async def execute(
        self,
        input_model: BaseModel,
        tenant_context: TenantContext,
        session: AsyncSession | None,
    ) -> ToolResult:
        """Save/update the dialog's lead inside the caller's transaction."""
        args = SaveLeadRealInput.model_validate(input_model)

        if session is None:
            logger.warning("tool.save_lead.no_session", tool_name=self.name)
            return ToolResult(ok=False, error_message=_SAVE_LEAD_UNAVAILABLE)

        scope = get_lead_turn_scope()
        if scope is None:
            logger.warning("tool.save_lead.no_dialog_scope", tool_name=self.name)
            return ToolResult(ok=False, error_message=_SAVE_LEAD_UNAVAILABLE)

        try:
            use_case = SaveLeadUseCase(session, tenant_context)
            service_id = await use_case.resolve_service_id(args.service_name)
            lead_input = LeadInput(
                contact_phone=args.contact_phone,
                contact_username=args.contact_username,
                summary=args.intent,
                comment=args.comment,
                service_id=service_id,
            )
            outcome = await use_case.save(
                lead_input,
                conversation_id=scope.conversation_id,
                notifications=scope.notifications,
            )
        except Exception:  # noqa: BLE001 - fail closed, no raw text to the model
            logger.error("tool.save_lead.failed", tool_name=self.name)
            return ToolResult(ok=False, error_message=_SAVE_LEAD_UNAVAILABLE)

        return self.build_result(
            SaveLeadRealOutput(
                lead_id=outcome.lead_id,
                conversation_id=outcome.conversation_id,
                status=LeadStatus(outcome.status).value,
                is_update=outcome.is_update,
            ).model_dump()
        )
