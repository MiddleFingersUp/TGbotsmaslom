"""Unit tests for the deterministic conversation state machine (Stage 5.2)."""

from __future__ import annotations

import pytest

from src.agent_core.state_machine import initial_state, transition
from src.db.models.enums import ConversationState

pytestmark = [pytest.mark.agent, pytest.mark.unit]


@pytest.mark.unit
class TestStateMachine:
    """Transition table per docs/ARCHITECTURE.md §7 / DEVELOPMENT_PLAN Stage 5."""

    def test_initial_state_is_greeting(self) -> None:
        assert initial_state() == ConversationState.GREETING

    def test_no_tools_first_reply_greeting_to_need_discovery(self) -> None:
        result = transition(ConversationState.GREETING, [], had_agent_reply=True)
        assert result == ConversationState.NEED_DISCOVERY

    def test_no_tools_no_reply_keeps_state(self) -> None:
        assert transition(ConversationState.CONSULTATION, [], had_agent_reply=False) == (
            ConversationState.CONSULTATION
        )

    @pytest.mark.parametrize(
        ("tool_name", "expected"),
        [
            ("search_services", ConversationState.CONSULTATION),
            ("search_faq", ConversationState.CONSULTATION),
            ("save_lead", ConversationState.LEAD_COLLECTION),
            ("handoff_request", ConversationState.HANDOFF),
        ],
    )
    def test_single_tool_transition(self, tool_name: str, expected: ConversationState) -> None:
        result = transition(ConversationState.NEED_DISCOVERY, [tool_name], had_agent_reply=True)
        assert result == expected

    def test_handout_priority_wins_over_other_tools(self) -> None:
        """handoff_request takes precedence over search/save in the same turn."""
        result = transition(
            ConversationState.NEED_DISCOVERY,
            ["search_services", "save_lead", "handoff_request"],
            had_agent_reply=True,
        )
        assert result == ConversationState.HANDOFF

    def test_lead_beats_search_when_both_used(self) -> None:
        result = transition(
            ConversationState.NEED_DISCOVERY,
            ["search_services", "save_lead"],
            had_agent_reply=True,
        )
        assert result == ConversationState.LEAD_COLLECTION

    def test_unknown_tools_do_not_change_state(self) -> None:
        result = transition(
            ConversationState.CONSULTATION, ["weather lookup"], had_agent_reply=True
        )
        assert result == ConversationState.CONSULTATION

    def test_stays_handoff_after_interception(self) -> None:
        result = transition(ConversationState.HANDOFF, ["handoff_request"], had_agent_reply=True)
        assert result == ConversationState.HANDOFF

    def test_never_transitions_to_closed_from_tool_path(self) -> None:
        """`closed` is backend-only; no LLM trajectory can produce it."""
        tools = [
            "search_services",
            "search_faq",
            "save_lead",
            "handoff_request",
            "unknown",
        ]
        for state in ConversationState:
            if state == ConversationState.CLOSED:
                continue
            for name in tools:
                result = transition(state, [name], had_agent_reply=True)
                assert result != ConversationState.CLOSED

    def test_closed_is_absorbing(self) -> None:
        assert transition(ConversationState.CLOSED, ["save_lead"], had_agent_reply=True) == (
            ConversationState.CLOSED
        )
