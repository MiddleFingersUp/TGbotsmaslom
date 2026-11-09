"""Deterministic test stubs for the four agent business tools.

**These stubs are replaced by real implementations in Stages 6–7**
(search & knowledge retrieval → Stage 6; leads & handoff → Stage 7).
They perform NO database access and return deterministic data so that the
agent loop (Stage 5.2) can be built and tested end-to-end without the
knowledge base.

Security contract (docs/SECURITY.md §13): every stub validates its input
through a Pydantic `input_schema` and its own output through an
`output_schema`; results never contain secrets or personal data.
"""

from __future__ import annotations

import uuid
from typing import ClassVar, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.context import TenantContext
from src.tools.base import Tool
from src.tools.registry import ToolRegistry
from src.tools.schemas import ToolResult

_UUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "tgbots-tools-stubs")


class _ServiceSummary(BaseModel):
    """One service card returned by the stub search."""

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    price_text: str = Field(min_length=1)


class SearchServicesInput(BaseModel):
    """Arguments for the `search_services` tool."""

    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=5, ge=1, le=10)


class SearchServicesOutput(BaseModel):
    """Deterministic stub output for `search_services`."""

    items: list[_ServiceSummary]
    query: str
    stub: bool = True


class SearchServicesTool(Tool):
    """Stub of the services-catalog search tool. Replaced in Stage 6."""

    name: ClassVar[str] = "search_services"
    description: ClassVar[str] = (
        "Поиск по каталогу услуг компании. Возвращает подходящие услуги с описанием и ценами."
    )
    input_schema: ClassVar[type[BaseModel]] = SearchServicesInput
    output_schema: ClassVar[type[BaseModel]] = SearchServicesOutput

    async def execute(
        self,
        input_model: BaseModel,
        tenant_context: TenantContext,
        session: AsyncSession | None,
    ) -> ToolResult:
        """Return one deterministic card echoing the query (no DB access)."""
        args = SearchServicesInput.model_validate(input_model)
        return self.build_result(
            SearchServicesOutput(
                items=[
                    _ServiceSummary(
                        name=f"[stub] Услуга по запросу «{args.query[:60]}»",
                        description="[stub] Тестовое описание услуги в рамках этапа 5.",
                        price_text="по каталогу",
                    )
                ],
                query=args.query,
            )
        )


class SearchFAQInput(BaseModel):
    """Arguments for the `search_faq` tool."""

    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=3, ge=1, le=10)


class FAQItem(BaseModel):
    """One FAQ answer card."""

    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)


class SearchFAQOutput(BaseModel):
    """Deterministic stub output for `search_faq`."""

    answers: list[FAQItem]
    query: str
    stub: bool = True


class SearchFAQTool(Tool):
    """Stub of the FAQ search tool. Replaced in Stage 6."""

    name: ClassVar[str] = "search_faq"
    description: ClassVar[str] = (
        "Поиск по базе частых вопросов и ответов: правила, расписания, общие вопросы."
    )
    input_schema: ClassVar[type[BaseModel]] = SearchFAQInput
    output_schema: ClassVar[type[BaseModel]] = SearchFAQOutput

    async def execute(
        self,
        input_model: BaseModel,
        tenant_context: TenantContext,
        session: AsyncSession | None,
    ) -> ToolResult:
        """Return one deterministic FAQ card built from the query (no DB access)."""
        args = SearchFAQInput.model_validate(input_model)
        return self.build_result(
            SearchFAQOutput(
                answers=[
                    FAQItem(
                        question=args.query,
                        answer="[stub] Тестовый ответ на часто задаваемый вопрос.",
                    )
                ],
                query=args.query,
            )
        )


class SaveLeadInput(BaseModel):
    """Arguments for the `save_lead` tool."""

    intent: str = Field(min_length=1, max_length=1000)
    contact_phone: str | None = None
    contact_username: str | None = None
    service_name: str | None = None

    @model_validator(mode="after")
    def requires_contact(self) -> SaveLeadInput:
        """A lead must carry at least one contact channel (PRODUCT_SPEC FR-5.1)."""
        if not self.contact_phone and not self.contact_username:
            raise ValueError("At least one of contact_phone / contact_username is required.")
        return self


class SaveLeadOutput(BaseModel):
    """Deterministic stub output for `save_lead`."""

    lead_id: str
    status: Literal["new"] = "new"
    stub: bool = True


class SaveLeadTool(Tool):
    """Stub of the lead-saving tool. Replaced in Stage 7."""

    name: ClassVar[str] = "save_lead"
    description: ClassVar[str] = (
        "Сохранить заявку клиента. Вызывай только после получения контакта от клиента."
    )
    input_schema: ClassVar[type[BaseModel]] = SaveLeadInput
    output_schema: ClassVar[type[BaseModel]] = SaveLeadOutput

    async def execute(
        self,
        input_model: BaseModel,
        tenant_context: TenantContext,
        session: AsyncSession | None,
    ) -> ToolResult:
        """Produce a deterministic pseudo-lead id (uuid5, no DB access).

        The tenant scope participates in the uuid5 hash: the same request in
        a different tenant yields a different id.
        """
        args = SaveLeadInput.model_validate(input_model)
        key = "|".join(
            [
                str(tenant_context.tenant_id),
                args.intent,
                args.contact_phone or "",
                args.contact_username or "",
                args.service_name or "",
            ]
        )
        lead_id = uuid.uuid5(_UUID_NAMESPACE, key).hex
        return self.build_result(SaveLeadOutput(lead_id=lead_id))


class HandoffRequestInput(BaseModel):
    """Arguments for the `handoff_request` tool."""

    trigger: Literal["client_request", "agent_decision", "system_error"]
    note: str | None = Field(default=None, max_length=500)


class HandoffRequestOutput(BaseModel):
    """Deterministic stub output for `handoff_request`."""

    trigger: Literal["client_request", "agent_decision", "system_error"]
    status: Literal["active"] = "active"
    stub: bool = True


class HandoffRequestTool(Tool):
    """Stub of the human-handoff request tool. Replaced in Stage 7."""

    name: ClassVar[str] = "handoff_request"
    description: ClassVar[str] = (
        "Передать диалог живому сотруднику: по просьбе клиента или при сложном вопросе."
    )
    input_schema: ClassVar[type[BaseModel]] = HandoffRequestInput
    output_schema: ClassVar[type[BaseModel]] = HandoffRequestOutput

    async def execute(
        self,
        input_model: BaseModel,
        tenant_context: TenantContext,
        session: AsyncSession | None,
    ) -> ToolResult:
        """Return a deterministic active-handoff marker (no DB access)."""
        args = HandoffRequestInput.model_validate(input_model)
        return self.build_result(HandoffRequestOutput(trigger=args.trigger))


def build_default_stub_registry() -> ToolRegistry:
    """Convenience factory: a registry preloaded with the four stubs."""
    registry = ToolRegistry()
    registry.register(SearchServicesTool())
    registry.register(SearchFAQTool())
    registry.register(SaveLeadTool())
    registry.register(HandoffRequestTool())
    return registry
