"""Knowledge base: retrieval interface and PostgreSQL full-text search."""

from src.knowledge.retrieval import (
    FaqHit,
    RetrievalService,
    ServiceHit,
    ServiceSearchFilters,
    clamp_top_k,
)
from src.knowledge.search import PostgresRetrievalService

__all__ = [
    "FaqHit",
    "PostgresRetrievalService",
    "RetrievalService",
    "ServiceHit",
    "ServiceSearchFilters",
    "clamp_top_k",
]
