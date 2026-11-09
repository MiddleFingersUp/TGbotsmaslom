"""Unit tests for retrieval interface and PostgreSQL FTS query generation.

Offline: queries are generated then compiled WITHOUT a real PostgreSQL
connection; the SQL text and bound values are asserted directly.
Tenant isolation is verified at the generated-SQL level (DATABASE.md §9).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.db.context import TenantContext
from src.db.models.faqs import FAQ
from src.db.models.services import Service
from src.db.repositories.repositories import FAQRepository, ServiceRepository
from src.knowledge.retrieval import (
    FaqHit,
    RetrievalService,
    ServiceHit,
    ServiceSearchFilters,
    clamp_top_k,
)
from src.knowledge.search import PostgresRetrievalService


def _tenant() -> TenantContext:
    return TenantContext(tenant_id=uuid.uuid4())


def _compiled(statement: Any) -> str:
    """Compile with literal binds so SQL text can be asserted."""
    return str(statement.compile(compile_kwargs={"literal_binds": True}))


def _no_rows_result() -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    return result


def _rows_result(rows: list[Any]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    return result


@pytest.mark.unit
class TestClampTopK:
    """top_k clipped into 1..10 (docs/ARCHITECTURE.md §10)."""

    @pytest.mark.parametrize(
        ("raw", "clamped"),
        [(0, 1), (-5, 1), (1, 1), (5, 5), (10, 10), (11, 10), (999, 10)],
    )
    def test_clipping(self, raw: int, clamped: int) -> None:
        assert clamp_top_k(raw) == clamped


@pytest.mark.unit
class TestServiceRepositoryFTS:
    """Generated SQL: russian config, tenant isolation, active/expiry filters."""

    async def test_sql_shape(self) -> None:
        session = AsyncMock()
        session.execute.return_value = _no_rows_result()
        repo = ServiceRepository(session, _tenant())
        await repo.fulltext_search("стрижка женская", ["стрижка"], 5, today=None)

        sql = _compiled(session.execute.call_args[0][0])
        assert "to_tsvector('russian'::regconfig" in sql  # russian language config
        assert "plainto_tsquery('russian'::regconfig" in sql  # safe user-text parsing
        assert "@@" in sql  # match operator
        assert "ts_rank" in sql  # ranking
        assert "services.tenant_id" in sql  # tenant isolation (§9)
        assert "services.status" in sql
        assert "services.valid_until" in sql
        assert "ORDER BY" in sql and "ts_rank" in sql
        assert "LIMIT 5" in sql

    async def test_tags_and_condition(self) -> None:
        """Every requested tag yields an AND-contained ARRAY condition."""
        session = AsyncMock()
        session.execute.return_value = _no_rows_result()
        repo = ServiceRepository(session, _tenant())
        await repo.fulltext_search("стрижка", ["стрижка", "женская"], 5)

        sql = _compiled(session.execute.call_args[0][0])
        assert sql.count("@>") == 2  # ARRAY containment per tag
        assert "WHERE" in sql
        assert " AND " in sql

    async def test_tenant_id_is_bound_from_context(self) -> None:
        """tenant_id comes from the context as a bound parameter, not literals."""
        context = _tenant()
        session = AsyncMock()
        session.execute.return_value = _no_rows_result()
        repo = ServiceRepository(session, context)
        await repo.fulltext_search("массаж", [], 3)

        statement = session.execute.call_args[0][0]
        params = statement.compile().params
        assert str(context.tenant_id) in str(params)

    async def test_top_k_clamped_in_sql(self) -> None:
        session = AsyncMock()
        session.execute.return_value = _no_rows_result()
        repo = ServiceRepository(session, _tenant())
        await repo.fulltext_search("стрижка", [], 999)
        assert "LIMIT 10" in _compiled(session.execute.call_args[0][0])

        session = AsyncMock()
        session.execute.return_value = _no_rows_result()
        repo = ServiceRepository(session, _tenant())
        await repo.fulltext_search("стрижка", [], -3)
        assert "LIMIT 1" in _compiled(session.execute.call_args[0][0])

    async def test_blank_query_returns_empty_without_db_hit(self) -> None:
        """Empty/whitespace query → честный пустой результат, БД не запрашивается."""
        session = AsyncMock()
        repo = ServiceRepository(session, _tenant())
        result = await repo.fulltext_search("   ", [], 5)

        assert result == []
        session.execute.assert_not_awaited()


@pytest.mark.unit
class TestFAQRepositoryFTS:
    """FAQ search: question+answer index, active filter, tenant boundary."""

    async def test_sql_shape(self) -> None:
        session = AsyncMock()
        session.execute.return_value = _no_rows_result()
        repo = FAQRepository(session, _tenant())
        await repo.fulltext_search("работаете ли в воскресенье", 3)

        sql = _compiled(session.execute.call_args[0][0])
        assert "to_tsvector('russian'" in sql
        assert "faqs.question" in sql and "faqs.answer" in sql
        assert "faqs.tenant_id" in sql  # tenant isolation
        assert "faqs.status" in sql
        assert "LIMIT 3" in sql

    async def test_question_and_answer_concat(self) -> None:
        """Index covers both question and answer per DATABASE.md §4.12."""
        session = AsyncMock()
        session.execute.return_value = _no_rows_result()
        repo = FAQRepository(session, _tenant())
        await repo.fulltext_search("график", 3)
        sql = _compiled(session.execute.call_args[0][0])
        assert "concat(faqs.question, ' ', faqs.answer)" in sql

    async def test_blank_query_returns_empty(self) -> None:
        session = AsyncMock()
        repo = FAQRepository(session, _tenant())
        assert await repo.fulltext_search("", 3) == []
        session.execute.assert_not_awaited()


@pytest.mark.unit
class TestRetrievalService:
    """RetrievalService orchestration over repositories (hits, honesty)."""

    def _service_row(self, tenant_id: uuid.UUID | None = None) -> Service:
        return Service(
            tenant_id=tenant_id or uuid.uuid4(),
            name="Стрижка женская",
            description="Модельная стрижка с мытьём головы и укладкой.",
            price_min=Decimal("1500.00"),
            price_max=None,
            currency="RUB",
            duration_minutes=60,
            tags=["стрижка"],
        )

    def _faq_row(self) -> FAQ:
        return FAQ(
            tenant_id=uuid.uuid4(),
            question="Вы работаете в воскресенье?",
            answer="Да, салон работает ежедневно с 9:00 до 21:00.",
            tags=["график"],
        )

    async def test_search_services_maps_hits(self) -> None:
        session = AsyncMock()
        session.execute.return_value = _rows_result([self._service_row()])
        service: RetrievalService = PostgresRetrievalService(session, _tenant())

        hits = await service.search_services("стрижка", None, 5)

        assert len(hits) == 1
        hit = hits[0]
        assert isinstance(hit, ServiceHit)
        assert hit.name == "Стрижка женская"
        assert hit.price_text == "от 1500.00 RUB"  # honest price from the row
        assert hit.tags == ["стрижка"]
        assert "secret" not in hit.model_dump_json().lower()

    async def test_price_variants_rendering(self) -> None:
        session = AsyncMock()
        row = self._service_row()
        row.price_min = Decimal("4000.00")
        row.price_max = Decimal("6000.00")
        session.execute.return_value = _rows_result([row])
        service: RetrievalService = PostgresRetrievalService(session, _tenant())

        hits = await service.search_services(
            "окрашивание", ServiceSearchFilters(tags=["окрашивание"]), 5
        )
        assert hits[0].price_text == "от 4000.00 до 6000.00 RUB"

    async def test_price_by_request_when_no_digits(self) -> None:
        session = AsyncMock()
        row = self._service_row()
        row.price_min = None
        session.execute.return_value = _rows_result([row])
        service: RetrievalService = PostgresRetrievalService(session, _tenant())
        hits = await service.search_services("какая цена?", None, 5)
        assert hits[0].price_text == "цена по запросу"

    async def test_blank_query_is_honest_empty(self) -> None:
        """Пустой запрос → пустой список, никаких фолбэков на каталог."""
        session = AsyncMock()
        service: RetrievalService = PostgresRetrievalService(session, _tenant())
        assert await service.search_services("   ", None, 5) == []
        assert await service.search_faq("", 5) == []
        session.execute.assert_not_awaited()

    async def test_empty_db_result_is_honest_empty(self) -> None:
        """Нет совпадений → [] (никогда не полные каталоги)."""
        session = AsyncMock()
        session.execute.return_value = _no_rows_result()
        service: RetrievalService = PostgresRetrievalService(session, _tenant())
        assert await service.search_services("несуществующая услуга", None, 5) == []
        assert await service.search_faq("нет такого вопроса", 3) == []

    async def test_search_faq_maps_hits(self) -> None:
        session = AsyncMock()
        session.execute.return_value = _rows_result([self._faq_row()])
        service: RetrievalService = PostgresRetrievalService(session, _tenant())
        hits = await service.search_faq("воскресенье", 3)

        assert len(hits) == 1
        hit = hits[0]
        assert isinstance(hit, FaqHit)
        assert hit.question.startswith("Вы работаете")
        assert "9:00" in hit.answer

    async def test_tenant_isolation_via_context_only(self) -> None:
        """Tenant context drives the WHERE clause as a bound parameter."""
        context = _tenant()
        session = AsyncMock()
        session.execute.return_value = _rows_result([self._service_row(context.tenant_id)])
        service: RetrievalService = PostgresRetrievalService(session, context)
        await service.search_services("стрижка", None, 5)

        statement = session.execute.call_args[0][0]
        params = statement.compile().params
        assert str(context.tenant_id) in str(params)
