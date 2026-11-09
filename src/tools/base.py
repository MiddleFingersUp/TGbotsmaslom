"""Tool abstraction for the agent execution layer (docs/ARCHITECTURE.md §11).

A tool is the ONLY way an LLM can affect business data: the provider merely
*nominates* a call, backend code (this layer) validates inputs/outputs with
Pydantic schemas and enforces the tenant context (docs/SECURITY.md §13).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.context import TenantContext
from src.tools.schemas import ToolError, ToolResult


class Tool(ABC):
    """Base class for all agent-callable business tools.

    Class-level contract:
    - `name`          — registry key, also used in `agents.allowed_tools`;
    - `description`   — shown to the LLM via `ToolSpec`;
    - `input_schema`  — Pydantic model the arguments are validated against;
    - `output_schema` — Pydantic model the result data is validated against.

    Implementations MUST stay tenant-scoped: they receive the `TenantContext`
    validated for the current update and may only touch `session` within it.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[type[BaseModel]]
    output_schema: ClassVar[type[BaseModel]]

    @abstractmethod
    async def execute(
        self,
        input_model: BaseModel,
        tenant_context: TenantContext,
        session: AsyncSession | None,
    ) -> ToolResult:
        """Execute the tool with a validated input and return a structured result."""

    def validate_input(self, raw_arguments: dict[str, Any]) -> BaseModel:
        """Validate raw LLM-provided arguments against `input_schema` (fail closed)."""
        try:
            return self.input_schema.model_validate(raw_arguments)
        except ValidationError as error:
            raise ToolError(
                "input validation failed",
                tool_name=self.name,
            ) from error

    def build_result(self, raw_output: Any) -> ToolResult:
        """Validate the tool's own output against `output_schema` and wrap it."""
        if raw_output is None:
            return ToolResult(ok=True)
        validated = self.output_schema.model_validate(raw_output)
        return ToolResult(ok=True, data=validated.model_dump())
