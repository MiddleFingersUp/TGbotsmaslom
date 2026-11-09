"""PostgreSQL full-text retrieval implementation (docs/ARCHITECTURE.md §10).

Wraps `ServiceRepository`/`FAQRepository` full-text searches behind the
narrow `RetrievalService` interface. Stages:

- Stage 6.1 (this file): PostgreSQL full-text search with the `russian`
  language configuration, `plainto_tsquery` for safe parsing of user text,
  `ts_rank` ordering;
- a future Stage can replace THIS FILE ONLY with a pgvector/hybrid
  implementation (docs/DATABASE.md FR-3.6) without touching Agent Core,
  the tool registry, or the LLM layer.

Honesty rules (see retrieval.py): empty query → empty list, no fabricated
data, hit models carry only non-sensitive fields.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

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
from src.monitoring.logging import get_logger

logger = get_logger("knowledge.search")


class PostgresRetrievalService(RetrievalService):
    """Knowledge retrieval backed by tenant-scoped PostgreSQL repositories."""

    def __init__(self, session: AsyncSession, tenant_context: TenantContext) -> None:
        self.session = session
        self.tenant_context = tenant_context

    async def search_services(
        self,
        query: str,
        filters: ServiceSearchFilters | None = None,
        top_k: int = 5,
    ) -> list[ServiceHit]:
        """Search active tenant services; tags filter AND-combined."""
        effective_filters = filters if filters is not None else ServiceSearchFilters()
        cleaned_query = query.strip()
        if not cleaned_query:
            return []
        limits = clamp_top_k(top_k)

        repository = ServiceRepository(self.session, self.tenant_context)
        rows = await repository.fulltext_search(
            cleaned_query,
            list(effective_filters.tags),
            limits,
        )
        hits = [_service_hit(row) for row in rows]
        logger.debug(
            "knowledge.search_services",
            hits=len(hits),
            top_k=limits,
            tags=len(effective_filters.tags),
        )
        return hits

    async def search_faq(self, query: str, top_k: int = 3) -> list[FaqHit]:
        """Search active tenant FAQ entries."""
        cleaned_query = query.strip()
        if not cleaned_query:
            return []
        limits = clamp_top_k(top_k)

        repository = FAQRepository(self.session, self.tenant_context)
        rows = await repository.fulltext_search(cleaned_query, limits)
        hits = [_faq_hit(row) for row in rows]
        logger.debug("knowledge.search_faq", hits=len(hits), top_k=limits)
        return hits


def _service_hit(row: Service) -> ServiceHit:
    """Map a DB row to the non-sensitive hit model (price as display text)."""
    return ServiceHit(
        service_id=str(row.id),
        name=row.name,
        description=row.description,
        price_text=_format_price(row),
        tags=list(row.tags),
    )


def _faq_hit(row: FAQ) -> FaqHit:
    return FaqHit(
        faq_id=str(row.id),
        question=row.question,
        answer=row.answer,
        tags=list(row.tags),
    )


def _format_price(row: Service) -> str:
    """Render a stable human-readable price line (no currency guessing)."""
    if row.price_min is None and row.price_max is None:
        return "цена по запросу"
    if row.price_max is None:
        return f"от {row.price_min} {row.currency}"
    if row.price_min == row.price_max:
        return f"{row.price_min} {row.currency}"
    return f"от {row.price_min} до {row.price_max} {row.currency}"
