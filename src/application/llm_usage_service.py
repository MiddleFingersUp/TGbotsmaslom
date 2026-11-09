"""Application service persisting LLM token usage (docs/DATABASE.md §4.13).

The provider layer never touches the database: it returns `TokenUsage` inside
`LLMResponse`, and this application-layer service decides whether and how to
record it. Usage with missing counters is not persisted and never guessed.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.context import TenantContext
from src.db.models.token_usage import TokenUsage as TokenUsageRow
from src.db.repositories.repositories import TokenUsageRepository
from src.llm_providers.schemas import LLMResponse
from src.monitoring.logging import get_logger

logger = get_logger("application.llm_usage_service")


class TokenUsageService:
    """Records completed LLM call statistics for tenant accounting."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        tenant_context: TenantContext,
        response: LLMResponse,
        conversation_id: UUID | None = None,
    ) -> TokenUsageRow | None:
        """Persist usage row if the provider reported complete counters.

        Returns the persisted row, or None when the provider did not report
        usage (absence is logged at debug level, never fabricated).
        """
        usage = response.usage

        if not usage.is_available:
            logger.debug(
                "llm.usage.absent",
                provider=response.provider,
                model=response.model,
            )
            return None

        if usage.prompt_tokens is None or usage.completion_tokens is None:
            logger.debug(
                "llm.usage.incomplete",
                provider=response.provider,
                model=response.model,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
            )
            return None

        repository = TokenUsageRepository(self.session, tenant_context)
        row = TokenUsageRow(
            provider=response.provider,
            model=response.model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            conversation_id=conversation_id,
        )
        await repository.add(row)
        return row
