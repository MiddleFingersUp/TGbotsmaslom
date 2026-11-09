"""Tool registry with per-tenant whitelist enforcement (docs/ARCHITECTURE.md §11).

Rules implemented here:
- a tool is resolvable by name ONLY if it is registered AND listed in the
  tenant's `agents.allowed_tools` whitelist;
- both "not registered" and "not whitelisted" produce a single
  "unknown tool" failure, so the LLM cannot enumerate the tool set;
- `MAX_TOOL_CALLS_PER_TURN` bounds the agent loop's tool-call attempts per
  LLM turn (docs/DEVELOPMENT_PLAN.md Stage 5, ARCHITECTURE §7);
- execution failures never leak raw exception text: the agent loop and the
  logs receive structured, safe messages (docs/SECURITY.md §13).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.context import TenantContext
from src.llm_providers.schemas import ToolSpec
from src.monitoring.logging import get_logger
from src.tools.base import Tool
from src.tools.schemas import ToolError, ToolResult

logger = get_logger("tools.registry")

MAX_TOOL_CALLS_PER_TURN = 5


def _tool_spec_from_tool(tool: Tool) -> ToolSpec:
    """Build a provider-agnostic `ToolSpec` from a Tool's class contract."""
    return ToolSpec(
        name=tool.name,
        description=tool.description,
        parameters_json_schema=tool.input_schema.model_json_schema(),
    )


class ToolRegistry:
    """Name-keyed registry with whitelist-filtered lookups and spec export."""

    def __init__(self) -> None:
        """Create an empty registry; tools are added via `register`."""
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool; duplicate names are a configuration error."""
        if tool.name in self._tools:
            raise ToolError(f"tool '{tool.name}' is already registered", tool_name=tool.name)
        self._tools[tool.name] = tool

    def get(self, name: str, allowed_tools: list[str]) -> Tool:
        """Resolve a tool by name strictly within the tenant whitelist.

        Unknown and non-whitelisted names are indistinguishable on purpose:
        a single "unknown tool" failure prevents tool-set enumeration by the
        model (docs/ARCHITECTURE.md §11, docs/SECURITY.md §13).
        """
        tool = self._tools.get(name)
        if tool is None or name not in allowed_tools:
            raise ToolError("unknown tool", tool_name=name)
        return tool

    def get_specs(self, allowed_tools: list[str]) -> list[ToolSpec]:
        """Export `ToolSpec` objects for every whitelisted registered tool."""
        specs: list[ToolSpec] = []
        for name in allowed_tools:
            tool = self._tools.get(name)
            if tool is not None:
                specs.append(_tool_spec_from_tool(tool))
        return specs

    def names(self) -> list[str]:
        """Return registered tool names (diagnostics only, not whitelisting)."""
        return sorted(self._tools)


async def run_tool(
    registry: ToolRegistry,
    name: str,
    arguments: dict[str, Any],
    *,
    allowed_tools: list[str],
    tenant_context: TenantContext,
    session: AsyncSession | None,
) -> ToolResult:
    """Validate arguments, execute the tool, and validate its output.

    Failure semantics (docs/ARCHITECTURE.md §11):
    - unknown/not-whitelisted tool → `ToolError` raised to the loop;
    - argument validation failure → `ToolResult(ok=False, ...)` fed back to
      the LLM so it can correct itself;
    - tool execution failure → `ToolResult(ok=False, ...)`; the raw
      exception details are never exposed to the LLM or to logs.
    """
    tool = registry.get(name, allowed_tools)

    try:
        input_model = tool.validate_input(arguments)
    except ToolError:
        logger.warning("tool.input_validation_failed", tool_name=name)
        return ToolResult(ok=False, error_message=f"Tool '{name}' input is invalid.")

    try:
        return await tool.execute(input_model, tenant_context, session)
    except ToolError as error:
        safe_message = (error.message or "tool failed").replace("\n", " ").replace("\r", " ")
        return ToolResult(ok=False, error_message=f"Tool '{name}' failed: {safe_message}")
    except Exception as error:  # noqa: BLE001 - fail_closed wrapper, details stay out of scope
        logger.error(
            "tool.execution_failed",
            tool_name=name,
            error_type=type(error).__name__,
        )
        return ToolResult(ok=False, error_message=f"Tool '{name}' failed.")
