"""Unit tests for ToolRegistry whitelist enforcement and unified execution."""

from __future__ import annotations

import uuid

import pytest

from src.db.context import TenantContext
from src.llm_providers.schemas import ToolSpec
from src.tools.base import Tool
from src.tools.registry import MAX_TOOL_CALLS_PER_TURN, ToolRegistry, run_tool
from src.tools.schemas import ToolError, ToolResult
from src.tools.stubs import (
    SearchServicesTool,
    build_default_stub_registry,
)

_ALLOWED_ALL = ["search_services", "search_faq", "save_lead", "handoff_request"]


class _ExplodingTool(Tool):
    """Stub tool whose execute() raises an unexpected exception."""

    name = "exploding"  # type: ignore[misc]
    description = "breaks on every call"
    input_schema = SearchServicesTool.input_schema
    output_schema = SearchServicesTool.output_schema

    async def execute(self, input_model, tenant_context, session) -> ToolResult:
        raise RuntimeError("secret internals leak attempt")


@pytest.mark.unit
class TestToolRegistry:
    """Whitelist-first lookups (docs/ARCHITECTURE.md §11)."""

    def test_register_and_get_within_whitelist(self) -> None:
        registry = build_default_stub_registry()
        tool = registry.get("save_lead", _ALLOWED_ALL)
        assert tool.name == "save_lead"

    def test_unknown_tool_raises(self) -> None:
        registry = build_default_stub_registry()
        with pytest.raises(ToolError, match="unknown tool"):
            registry.get("weather_lookup", _ALLOWED_ALL)

    def test_not_whitelisted_tool_is_indistinguishable_from_unknown(self) -> None:
        """Whitelist rejection uses the same message — no tool-set enumeration."""
        registry = build_default_stub_registry()
        with pytest.raises(ToolError, match="unknown tool"):
            registry.get("save_lead", allowed_tools=["search_services"])

    def test_duplicate_registration_rejected(self) -> None:
        registry = build_default_stub_registry()
        with pytest.raises(ToolError, match="already registered"):
            registry.register(SearchServicesTool())

    def test_get_specs_reflects_whitelist_only(self) -> None:
        registry = build_default_stub_registry()
        specs = registry.get_specs(allowed_tools=["search_faq"])
        assert len(specs) == 1
        spec = specs[0]
        assert isinstance(spec, ToolSpec)
        assert spec.name == "search_faq"
        assert "query" in spec.parameters_json_schema["properties"]

    def test_get_specs_skips_unregistered_names(self) -> None:
        registry = build_default_stub_registry()
        specs = registry.get_specs(allowed_tools=["search_faq", "not_registered"])
        assert len(specs) == 1

    def test_max_tool_calls_per_turn_constant(self) -> None:
        assert MAX_TOOL_CALLS_PER_TURN == 5

    def test_names_sorted(self) -> None:
        registry = build_default_stub_registry()
        assert registry.names() == sorted(_ALLOWED_ALL)


@pytest.mark.unit
class TestRunTool:
    """Unified validation/execution wrapper."""

    def _tenant(self) -> TenantContext:
        return TenantContext(tenant_id=uuid.uuid4())

    async def test_happy_path(self) -> None:
        registry = build_default_stub_registry()
        result = await run_tool(
            registry,
            "search_services",
            {"query": "стрижка"},
            allowed_tools=["search_services"],
            tenant_context=self._tenant(),
            session=None,
        )
        assert result.ok is True
        assert result.data is not None

    async def test_unknown_tool_raises(self) -> None:
        registry = build_default_stub_registry()
        with pytest.raises(ToolError, match="unknown tool"):
            await run_tool(
                registry,
                "save_lead",
                {},
                allowed_tools=["search_services"],  # not whitelisted
                tenant_context=self._tenant(),
                session=None,
            )

    async def test_invalid_input_returns_failure_result(self) -> None:
        """Argument validation failure → structured result fed back to the LLM."""
        registry = build_default_stub_registry()
        result = await run_tool(
            registry,
            "save_lead",
            {"intent": "запись"},  # no contact → schema violation
            allowed_tools=["save_lead"],
            tenant_context=self._tenant(),
            session=None,
        )
        assert result.ok is False
        assert result.error_message is not None
        assert "save_lead" in result.error_message

    async def test_unexpected_exception_never_leaks_internals(self) -> None:
        registry = ToolRegistry()
        registry.register(_ExplodingTool())
        tenant = self._tenant()
        result = await run_tool(
            registry,
            "exploding",
            {"query": "q"},
            allowed_tools=["exploding"],
            tenant_context=tenant,
            session=None,
        )
        assert result.ok is False
        assert result.error_message == "Tool 'exploding' failed."
        assert "secret" not in (result.error_message or "")
