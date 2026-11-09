"""Deterministic conversation-state transitions (docs/ARCHITECTURE.md §7).

The new state of a conversation is computed ONLY from backend facts of the
turn: which tools were invoked and whether an agent reply was produced.
The LLM can never set a state directly — `closed` cannot be reached from
here at all (it is a backend-only state, docs/DEVELOPMENT_PLAN.md Stage 5).

Transition table:
- `handoff_request` invoked       → HANDOFF      (backend intercepts the call)
- `save_lead` invoked             → LEAD_COLLECTION
- `search_services`/`search_faq`  → CONSULTATION
- no tools, first agent reply     → GREETING becomes NEED_DISCOVERY
- otherwise                       → state unchanged
"""

from __future__ import annotations

from src.db.models.enums import ConversationState

_TOOL_STATE_MAP: dict[str, ConversationState] = {
    "handoff_request": ConversationState.HANDOFF,
    "save_lead": ConversationState.LEAD_COLLECTION,
    "search_services": ConversationState.CONSULTATION,
    "search_faq": ConversationState.CONSULTATION,
}

# Resolution order when several tools were invoked within one turn.
_TOOL_PRIORITY: tuple[ConversationState, ...] = (
    ConversationState.HANDOFF,
    ConversationState.LEAD_COLLECTION,
    ConversationState.CONSULTATION,
)


def initial_state(agent: object | None = None) -> ConversationState:
    """Return the state for a brand-new conversation.

    The `agents` schema has no initial-state field (docs/DATABASE.md §4.3),
    so greeting is the platform constant; the parameter is kept for forward
    compatibility with a per-agent override.
    """
    return ConversationState.GREETING


def transition(
    current: ConversationState,
    tool_names: list[str],
    *,
    had_agent_reply: bool,
) -> ConversationState:
    """Compute the next state for one processed turn (deterministic)."""
    if current == ConversationState.CLOSED:
        return ConversationState.CLOSED

    candidates = [_TOOL_STATE_MAP[name] for name in tool_names if name in _TOOL_STATE_MAP]
    if candidates:
        for priority in _TOOL_PRIORITY:
            if priority in candidates:
                return priority

    if current == ConversationState.GREETING and had_agent_reply:
        return ConversationState.NEED_DISCOVERY
    return current
