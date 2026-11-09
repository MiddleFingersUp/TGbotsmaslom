"""Unit tests for tool result/validation contracts and stub input schemas."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from src.db.context import TenantContext
from src.tools.schemas import ToolError, ToolResult
from src.tools.stubs import (
    HandoffRequestTool,
    SaveLeadInput,
    SaveLeadTool,
    SearchFAQTool,
    SearchServicesTool,
)


@pytest.mark.unit
class TestToolResult:
    """ok/data/error_message invariants (docs/ARCHITECTURE.md §11)."""

    def test_ok_with_data(self) -> None:
        result = ToolResult(ok=True, data={"items": []})
        assert result.ok is True
        assert result.error_message is None

    def test_ok_without_data_allowed(self) -> None:
        result = ToolResult(ok=True)
        assert result.data is None

    def test_ok_with_error_message_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolResult(ok=True, error_message="oops")

    def test_failure_without_message_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolResult(ok=False)

    def test_failure_with_data_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolResult(ok=False, error_message="oops", data={"x": 1})

    def test_failure_message(self) -> None:
        result = ToolResult(ok=False, error_message="Tool 'x' input is invalid.")
        assert result.ok is False


@pytest.mark.unit
class TestToolError:
    """Registry-level error contract."""

    def test_message_and_tool_name(self) -> None:
        error = ToolError("unknown tool", tool_name="save_lead")
        assert error.message == "unknown tool"
        assert error.tool_name == "save_lead"


@pytest.mark.unit
class TestStubInputSchemas:
    """Input validation of the four deterministic stubs (docs/SECURITY.md §13)."""

    def test_search_services_rejects_empty_query(self) -> None:
        with pytest.raises(ValidationError):
            SearchServicesTool.input_schema.model_validate({"query": ""})

    def test_search_services_limit_bounds(self) -> None:
        args = SearchServicesTool.input_schema.model_validate({"query": "стрижка", "limit": 3})
        assert args.limit == 3
        with pytest.raises(ValidationError):
            SearchServicesTool.input_schema.model_validate({"query": "q", "limit": 0})
        with pytest.raises(ValidationError):
            SearchServicesTool.input_schema.model_validate({"query": "q", "limit": 11})

    def test_search_faq_defaults(self) -> None:
        args = SearchFAQTool.input_schema.model_validate({"query": "график"})
        assert args.limit == 3

    def test_save_lead_requires_a_contact(self) -> None:
        with pytest.raises(ValidationError):
            SaveLeadInput(intent="хочу стрижку")  # no contact at all

    def test_save_lead_accepts_username_only(self) -> None:
        args = SaveLeadInput(intent="хочу стрижку", contact_username="ivan")
        assert args.contact_username == "ivan"

    def test_save_lead_accepts_phone_only(self) -> None:
        args = SaveLeadInput(intent="хочу стрижку", contact_phone="+7 900 000-00-00")
        assert args.contact_phone == "+7 900 000-00-00"

    def test_handoff_trigger_enum(self) -> None:
        args = HandoffRequestTool.input_schema.model_validate({"trigger": "client_request"})
        assert args.trigger == "client_request"
        with pytest.raises(ValidationError):
            HandoffRequestTool.input_schema.model_validate({"trigger": "chaos"})


@pytest.mark.unit
class TestStubExecution:
    """Deterministic execution of the stubs (no DB access)."""

    def _tenant(self) -> TenantContext:
        return TenantContext(tenant_id=uuid.uuid4())

    async def test_search_services_deterministic(self) -> None:
        tool = SearchServicesTool()
        args = SearchServicesTool.input_schema.model_validate({"query": "стрижка"})
        first = await tool.execute(args, self._tenant(), None)
        second = await tool.execute(args, self._tenant(), None)

        assert first.ok is True
        assert first.data is not None
        assert first.data["query"] == "стрижка"
        assert first.data == second.data  # deterministic

    async def test_save_lead_stable_and_tenant_sealed(self) -> None:
        tool = SaveLeadTool()
        tenant = self._tenant()
        args = SaveLeadInput(intent="запись", contact_phone="+79000000000")

        first = await tool.execute(args, tenant, None)
        second = await tool.execute(args, tenant, None)
        other = await tool.execute(args, self._tenant(), None)

        assert first.ok and first.data is not None
        lead_id = first.data["lead_id"]
        assert first.data["lead_id"] == second.data["lead_id"]  # deterministic
        assert other.data is not None
        assert other.data["lead_id"] != lead_id  # tenant participates in id

    async def test_handoff_output_shape(self) -> None:
        tool = HandoffRequestTool()
        args = HandoffRequestTool.input_schema.model_validate({"trigger": "agent_decision"})
        result = await tool.execute(args, self._tenant(), None)

        assert result.ok is True
        assert result.data == {"trigger": "agent_decision", "status": "active", "stub": True}

    async def test_search_faq_output_validates(self) -> None:
        tool = SearchFAQTool()
        args = SearchFAQTool.input_schema.model_validate({"query": "работаете ли в воскресенье"})
        result = await tool.execute(args, self._tenant(), None)

        assert result.ok is True
        assert result.data is not None
        assert result.data["answers"][0]["question"] == "работаете ли в воскресенье"
