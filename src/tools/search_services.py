"""Real `search_services` tool backed by the Stage 6.1 retrieval layer.

Implementation of docs/DEVELOPMENT_PLAN.md Stage 6.2: the tool delegates to
`PostgresRetrievalService` (knowledge/search.py) using the AsyncSession and
TenantContext that the agent loop already provides to `run_tool` — the tool
itself opens NO new connections and contains NO retrieval logic.

Honesty contract (docs/ARCHITECTURE.md §10–11):
- hits come ONLY from the catalog; an empty result set is an explicit,
  structured outcome the LLM turns into a polite "не найдено" reply;
- SQL/any transport failures degrade to `ToolResult(ok=False)` with a
  generic Russian message — raw error text is never returned to the model
  (docs/SECURITY.md §13).
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.context import TenantContext
from src.knowledge.retrieval import (
    ServiceHit,
    ServiceSearchFilters,
)
from src.knowledge.search import PostgresRetrievalService
from src.monitoring.logging import get_logger
from src.tools.base import Tool
from src.tools.schemas import ToolResult

logger = get_logger("tools.search_services")


class SearchServicesRealInput(BaseModel):
    """Arguments for the real `search_services` tool invocation."""

    query: str = Field(min_length=1, max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=10)
    limit: int = Field(default=5, ge=1, le=10)


class SearchServicesRealOutput(BaseModel):
    """Tool output: catalog hits from a real retrieval call."""

    results: list[ServiceHit]
    message: str | None = None


class SearchServicesTool(Tool):
    """Real services-catalog search tool (POSTGRES retrieval, Stage 6.2).

    Registered instead of the Stage 5.1 stub in the production registry;
    the stub stays importable for offline unit tests.
    """

    name: ClassVar[str] = "search_services"
    description: ClassVar[str] = (
        "Поиск по каталогу услуг компании. Возвращает подходящие услуги с описанием и ценами."
    )
    input_schema: ClassVar[type[BaseModel]] = SearchServicesRealInput
    output_schema: ClassVar[type[BaseModel]] = SearchServicesRealOutput

    async def execute(
        self,
        input_model: BaseModel,
        tenant_context: TenantContext,
        session: AsyncSession | None,
    ) -> ToolResult:
        """Run the real catalog search and return a structured result."""
        args = SearchServicesRealInput.model_validate(input_model)

        if session is None:
            return ToolResult(ok=False, error_message="Поиск временно недоступен.")

        retrieval = PostgresRetrievalService(session, tenant_context)
        try:
            hits = await retrieval.search_services(
                args.query,
                ServiceSearchFilters(tags=list(args.tags)),
                args.limit,
            )
        except Exception:  # noqa: BLE001 - fail closed, no raw text to the model
            logger.warning("tool.search_services.db_failure", tool_name=self.name)
            return ToolResult(ok=False, error_message="Поиск временно недоступен.")

        message: str | None = None
        if not hits:
            message = "по каталогу ничего не найдено"

        return self.build_result(
            SearchServicesRealOutput(results=hits, message=message).model_dump()
        )
