"""Unit tests for the REAL retrieval tools (Stage 6.2; offline, mock sessions).

Behavioral guarantees tested here:
- catalog hits are mapped to `ToolResult(ok=True, data=...)` with
  non-sensitive fields only;
- an EMPTY result set is an honest, structured outcome (ok=True with a
  message the LLM turns into a polite refusal);
- SQL/transport failures degrade to `ok=False` with a generic Russian
  message — raw exception text is never exposed;
- the production registry registers the real tools (identifiers preserved
  for the Stage 5.1 whitelists) while keeping Stage-7 stubs.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.process_incoming_message import build_default_registry
from src.db.context import TenantContext
from src.db.models.services import Service
from src.tools.registry import ToolRegistry
from src.tools.schemas import ToolResult
from src.tools.search_faq import SearchFAQTool
from src.tools.search_services import SearchServicesTool as _RealServicesTool
from src.tools.stubs import HandoffRequestTool, SaveLeadTool


def _real_search_services_tool() -> Any:
    return _RealServicesTool()


def _tenant() -> TenantContext:
    return TenantContext(tenant_id=uuid.uuid4())


def _rows_result(rows: list[Any]) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = None
    scalars.all.return_value = rows
    result.scalars.return_value = scalars
    return result


def _no_rows_result() -> MagicMock:
    return _rows_result([])


def _service_row() -> Service:
    return Service(
        tenant_id=uuid.uuid4(),
        name="Стрижка женская",
        description="Модельная стрижка любой сложности с мытьём головы и укладкой.",
        price_min=Decimal("1500.00"),
        price_max=None,
        currency="RUB",
        duration_minutes=60,
        tags=["стрижка", "женская"],
    )


@pytest.mark.unit
@pytest.mark.asyncio
class TestRealSearchServicesTool:
    """Real catalog tool over the retrieval layer (mock session)."""

    async def test_hits_map_to_tool_result(self) -> None:
        session = AsyncMock()
        session.execute.return_value = _rows_result([_service_row()])
        tool = SearchFAQTool if False else _real_search_services_tool()
        result = await tool.execute(
            tool.input_schema.model_validate({"query": "стрижка"}),
            _tenant(),
            session,
        )

        assert result.ok is True
        data = result.data or {}
        results = data["results"]
        assert len(results) == 1
        assert results[0]["name"] == "Стрижка женская"
        assert results[0]["price_text"] == "от 1500.00 RUB"
        assert data["message"] is None

    async def test_empty_result_is_honest_structured(self) -> None:
        session = AsyncMock()
        session.execute.return_value = _no_rows_result()
        tool = _real_search_services_tool()
        result = await tool.execute(
            tool.input_schema.model_validate({"query": "полёт на марс"}),
            _tenant(),
            session,
        )

        assert result.ok is True
        assert result.data == {
            "results": [],
            "message": "по каталогу ничего не найдено",
        }

    async def test_blank_query_is_honest_empty(self) -> None:
        """Whitespace-only query → retrieval returns [] without a DB hit."""
        session = AsyncMock()
        tool = _real_search_services_tool()
        result = await tool.execute(
            tool.input_schema.model_validate({"query": "   "}),
            _tenant(),
            session,
        )
        assert result.ok is True
        assert (result.data or {}).get("message") == "по каталогу ничего не найдено"
        session.execute.assert_not_awaited()

    async def test_missing_session_fails_closed(self) -> None:
        tool = _real_search_services_tool()
        result = await tool.execute(
            tool.input_schema.model_validate({"query": "стрижка"}),
            _tenant(),
            None,
        )
        assert result.ok is False
        assert result.error_message == "Поиск временно недоступен."

    async def test_db_failure_never_leaks_raw_errors(self) -> None:
        session = AsyncMock()
        session.execute.side_effect = RuntimeError("boom with敏感 internals")
        tool = _real_search_services_tool()
        result = await tool.execute(
            tool.input_schema.model_validate({"query": "стрижка"}),
            _tenant(),
            session,
        )
        assert result.ok is False
        assert result.error_message == "Поиск временно недоступен."
        assert "敏感" not in (result.error_message or "")
        assert "boom" not in (result.error_message or "")

    async def test_tags_filter_reaches_repository(self) -> None:
        session = AsyncMock()
        session.execute.return_value = _rows_result([_service_row()])
        tool = _real_search_services_tool()
        await tool.execute(
            tool.input_schema.model_validate(
                {"query": "стрижка", "tags": ["женская"], "limit": 3}
            ),
            _tenant(),
            session,
        )
        # The tags filter must be forwarded into the FTS SQL generation.
        sql = session.execute.call_args[0][0]
        params = sql.compile().params
        assert any("женская" in str(value) for value in params.values())


def _real_search_services_tool() -> Any:
    """Import the real tool lazily to keep registry top-level clean."""
    from src.tools.search_services import SearchServicesTool

    return SearchServicesTool()


@pytest.mark.unit
@pytest.mark.asyncio
class TestRealSearchFAQTool:
    """Real FAQ tool over the retrieval layer (mock session)."""

    async def test_hits_map_to_tool_result(self) -> None:
        from src.db.models.faqs import FAQ

        session = AsyncMock()
        session.execute.return_value = _rows_result(
            [
                FAQ(
                    tenant_id=uuid.uuid4(),
                    question="Вы работаете в воскресенье?",
                    answer="Да, салон работает ежедневно с 9:00 до 21:00.",
                    tags=["график"],
                )
            ]
        )
        tool = SearchFAQTool()
        result = await tool.execute(
            tool.input_schema.model_validate({"query": "воскресенье"}),
            _tenant(),
            session,
        )

        assert result.ok is True
        results = (result.data or {}).get("results", [])
        assert results[0]["question"] == "Вы работаете в воскресенье?"
        assert (result.data or {}).get("message") is None

    async def test_empty_result_has_message(self) -> None:
        session = AsyncMock()
        session.execute.return_value = _no_rows_result()
        tool = SearchFAQTool()
        result = await tool.execute(
            tool.input_schema.model_validate({"query": "воскресенье"}),
            _tenant(),
            session,
        )
        assert result.ok is True
        data = result.data or {}
        assert data["results"] == []
        assert "не найден" in data["message"]

    async def test_db_failure_never_leaks_raw_errors(self) -> None:
        session = AsyncMock()
        session.execute.side_effect = RuntimeError("boom-internal")
        tool = SearchFAQTool()
        result = await tool.execute(
            tool.input_schema.model_validate({"query": "воскресенье"}),
            _tenant(),
            session,
        )
        assert result.ok is False
        assert result.error_message == "Поиск временно недоступен."
        assert "boom" not in (result.error_message or "")


@pytest.mark.unit
class TestProductionRegistry:
    """Production registry: real search tools + Stage-7 stubs."""

    def test_real_tools_replaced_the_search_stubs(self) -> None:
        from src.tools.search_faq import SearchFAQTool as FAQRealTool
        from src.tools.search_services import SearchServicesTool as ServicesRealTool

        registry = build_default_registry()

        assert isinstance(registry.get("search_services", ["search_services"]), ServicesRealTool)
        assert isinstance(registry.get("search_faq", ["search_faq"]), FAQRealTool)
        assert isinstance(registry.get("save_lead", ["save_lead"]), SaveLeadTool)
        assert isinstance(registry.get("handoff_request", ["handoff_request"]), HandoffRequestTool)
        assert sorted(registry.names()) == [
            "handoff_request",
            "save_lead",
            "search_faq",
            "search_services",
        ]
        assert isinstance(registry, ToolRegistry)
        assert ToolResult is not None
