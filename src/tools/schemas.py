"""Tool execution layer result and error contracts.

Per docs/ARCHITECTURE.md §11 and docs/SECURITY.md §13: every tool call made
by the agent is validated at backend level, and failures are always returned
to the agent loop as structured `ToolResult` objects (or raised as
`ToolError` for registry-level problems) — never as silent no-ops.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToolResult(BaseModel):
    """Structured outcome of a tool execution, safe to hand back to the LLM loop.

    Invariants:
    - `ok=True`  → `error_message` must be empty; `data` may still be None
      (e.g. `handoff_request` performs an action and returns metadata only);
    - `ok=False` → `error_message` is required, `data` must be empty.
    """

    model_config = ConfigDict(frozen=True)

    ok: bool
    data: dict[str, Any] | None = None
    error_message: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def invariants(self) -> ToolResult:
        """Enforce the ok/data/error_message invariants described above."""
        if self.ok and self.error_message:
            raise ValueError("error_message must be empty when ok is True.")
        if not self.ok and not self.error_message:
            raise ValueError("error_message is required when ok is False.")
        if not self.ok and self.data is not None:
            raise ValueError("data must be empty when ok is False.")
        return self


class ToolError(Exception):
    """Registry/validation-level tool failure (raised, not returned).

    Used for structural problems such as an unknown or disallowed tool name
    (ARCHITECTURE §11: "неизвестный инструмент → отказ"). `message` is safe
    for logs and never contains user content or secrets.
    """

    def __init__(self, message: str, *, tool_name: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.tool_name = tool_name
