"""Unit tests for retrieval interface contracts (hits, filters, honesty)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.knowledge.retrieval import (
    FaqHit,
    RetrievalService,
    ServiceHit,
    ServiceSearchFilters,
)
from src.knowledge.search import PostgresRetrievalService


@pytest.mark.unit
class TestHitModels:
    """Non-sensitive hit models accept only catalog fields."""

    def test_service_hit_ok(self) -> None:
        hit = ServiceHit(service_id="x", name="Стрижка", description="d", price_text="текст")
        assert hit.tags == []

    def test_service_hit_rejects_empty_name(self) -> None:
        with pytest.raises(ValidationError):
            ServiceHit(service_id="x", name="", description="d", price_text="текст")

    def test_faq_hit_ok(self) -> None:
        hit = FaqHit(faq_id="x", question="в?", answer="о", tags=["график"])
        assert hit.tags == ["график"]

    def test_faq_hit_rejects_empty_answer(self) -> None:
        with pytest.raises(ValidationError):
            FaqHit(faq_id="x", question="в?", answer="")

    def test_filters_forbid_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            ServiceSearchFilters(tags=[], admin_bypass=True)  # type: ignore[call-arg]


@pytest.mark.unit
class TestInterfaceContract:
    """PostgresRetrievalService conforms to the abstract RetrievalService."""

    def test_is_retrieval_service(self) -> None:
        from unittest.mock import AsyncMock

        service = PostgresRetrievalService(AsyncMock(), None)  # type: ignore[arg-type]
        assert isinstance(service, RetrievalService)

    async def test_abstract_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            RetrievalService()  # type: ignore[abstract]
