"""Retrieval interface for tenant knowledge (docs/ARCHITECTURE.md §10).

Contract notes:
- hits contain only NON-SENSITIVE catalog fields (no secrets, no PII;
  docs/SECURITY.md §7/§8) and never a raw DB row;
- empty results are HONEST: an empty query or no matches returns an empty
  list — the caller (agent loop) turns that into a polite refusal, the
  retrieval layer must never fabricate data nor fall back to dumping the
  full catalog;
- this interface is deliberately narrow so the Stage-5-pgvector/rag
  implementation described in docs/DATABASE.md §FR-3.6 can REPLACE the
  PostgreSQL full-text one WITHOUT touching Agent Core: only
  `src/knowledge/search.py` changes, tools wiring stays the same.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ServiceHit(BaseModel):
    """Non-sensitive service catalog hit fed to the LLM context."""

    model_config = ConfigDict(frozen=True)

    service_id: str
    name: str = Field(min_length=1)
    description: str
    price_text: str  # human-readable price line; may be "по запросу"
    tags: list[str] = Field(default_factory=list)


class FaqHit(BaseModel):
    """Non-sensitive FAQ hit fed to the LLM context."""

    model_config = ConfigDict(frozen=True)

    faq_id: str
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)


class ServiceSearchFilters(BaseModel):
    """Optional narrowing for service searches.

    `tags`: every tag listed here MUST be present on the service
    (AND semantics over PostgreSQL ARRAY containment). Expiry is always
    applied by the implementation (`valid_until >= today` or NULL).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tags: list[str] = Field(default_factory=list, max_length=10)

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict view (kept stable for callers that pass filters dict)."""
        return {"tags": list(self.tags)}


class RetrievalService(ABC):
    """Abstract knowledge retrieval contract (Stage 6.1: PostgreSQL FTS)."""

    @abstractmethod
    async def search_services(
        self,
        query: str,
        filters: ServiceSearchFilters | None = None,
        top_k: int = 5,
    ) -> list[ServiceHit]:
        """Return at most `top_k` active matching services within the tenant."""

    @abstractmethod
    async def search_faq(self, query: str, top_k: int = 3) -> list[FaqHit]:
        """Return at most `top_k` active matching FAQ answers within the tenant."""


def clamp_top_k(top_k: int) -> int:
    """Clip top-k into 1..10 (negative/oversized values are clamped, not rejected)."""
    return max(1, min(top_k, 10))
