"""Real `search_faq` tool backed by the Stage 6.1 retrieval layer.

Same contract as `src/tools/search_services.py`: delegates to
`PostgresRetrievalService` with the session and tenant context provided by
the agent loop; empty results are honest and structured; failures degrade
to a generic `ToolResult(ok=False)` without leaking raw error text.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.context import TenantContext
from src.knowledge.search import PostgresRetrievalService
from src.monitoring.logging import get_logger
from src.tools.base import Tool
from src.tools.schemas import ToolResult

logger = get_logger("tools.search_faq")


class SearchFAQRealInput(BaseModel):
    """Arguments for the real `search_faq` tool invocation."""

    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=3, ge=1, le=10)


class SearchFAQRealOutput(BaseModel):
    """Tool output: FAQ hits from a real retrieval call."""

    results: list[FaqHitModel]
    message: str | None = None


class FaqHitModel(BaseModel):
    """FAQ hit fields (plain-model re-export keeps the schema explicit)."""

    faq_id: str
    question: str
    answer: str
    tags: list[str]


class SearchFAQTool(Tool):
    """Real FAQ search tool (POSTGRES retrieval, Stage 6.2)."""

    name: ClassVar[str] = "search_faq"
    description: ClassVar[str] = (
        "Поиск по базе частых вопросов и ответов: правила, расписания, общие вопросы."
    )
    input_schema: ClassVar[type[BaseModel]] = SearchFAQRealInput
    output_schema: ClassVar[type[BaseModel]] = SearchFAQRealOutput

    async def execute(
        self,
        input_model: BaseModel,
        tenant_context: TenantContext,
        session: AsyncSession | None,
    ) -> ToolResult:
        """Run the real FAQ search and return a structured result."""
        args = SearchFAQRealInput.model_validate(input_model)

        if session is None:
            return ToolResult(ok=False, error_message="Поиск временно недоступен.")

        retrieval = PostgresRetrievalService(session, tenant_context)
        try:
            hits = await retrieval.search_faq(args.query, args.limit)
        except Exception:  # noqa: BLE001 - fail closed, no raw text to the model
            logger.warning("tool.search_faq.db_failure", tool_name=self.name)
            return ToolResult(ok=False, error_message="Поиск временно недоступен.")

        message: str | None = None
        if not hits:
            message = "по базе знаний ответ сейчас не найден; уточните у сотрудника"

        return self.build_result(
            SearchFAQRealOutput(
                results=[
                    FaqHitModel(
                        faq_id=hit.faq_id,
                        question=hit.question,
                        answer=hit.answer,
                        tags=list(hit.tags),
                    )
                    for hit in hits
                ],
                message=message,
            ).model_dump()
        )
