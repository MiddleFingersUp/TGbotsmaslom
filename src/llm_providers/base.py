"""Abstract LLM provider interface — the only contract the agent core may use."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from src.llm_providers.schemas import LLMRequest, LLMResponse


class LLMProvider(ABC):
    """Minimal async interface for chat completion providers.

    Implementations MUST be async, MUST NOT depend on Telegram or business
    logic, and MUST convert all transport failures into `LLMProviderError`
    subclasses (docs/ARCHITECTURE.md §8).
    """

    provider_name: ClassVar[str] = "base"

    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Send a chat completion request and return a normalized response."""

    async def aclose(self) -> None:  # noqa: B027 - optional hook; not all providers hold resources
        """Release underlying HTTP resources. Default: nothing to release."""

    async def __aenter__(self) -> LLMProvider:
        """Support async context manager usage."""
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Close the provider on exit."""
        await self.aclose()
