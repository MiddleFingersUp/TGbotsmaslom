"""Privacy filter integration point (see docs/SECURITY.md §8, SEC-OD-4).

This is NOT a fake full privacy policy: masking rules are deliberately
deferred to the agent context builder (Stage 5). The provider layer only
guarantees the hook exists so that no message bypasses privacy controls
once they are implemented.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.llm_providers.schemas import LLMRequest


class PrivacyFilter(ABC):
    """Contract for minimization/masking of data before it leaves for an external LLM."""

    @abstractmethod
    async def apply(self, request: LLMRequest) -> LLMRequest:
        """Return a request with personal data minimized or masked."""


class NoOpPrivacyFilter(PrivacyFilter):
    """Default placeholder: passes the request through unchanged.

    Replaced by a real implementation once SEC-OD-4 masking rules are fixed.
    """

    async def apply(self, request: LLMRequest) -> LLMRequest:
        """Return the request without any transformation."""
        return request
