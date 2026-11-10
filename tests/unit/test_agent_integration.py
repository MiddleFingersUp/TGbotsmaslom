"""Offline end-to-end integration for the agent turn (mock session, mock LLM).

Scenario: process_incoming_message → AgentLoop → reply persisted in
messages (role=AGENT, ai_model, meta), conversation state advanced,
token usage recorded through TokenUsageService — no real HTTP calls.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent_core.agent_loop import AgentLoop
from src.application.process_incoming_message import ProcessIncomingMessageUseCase
from src.db.models.agents import Agent
from src.db.models.conversations import Conversation
from src.db.models.enums import ConversationState, MessageRole
from src.db.models.messages import Message
from src.db.models.token_usage import TokenUsage as TokenUsageRow
from src.llm_providers.mock import MockLLMProvider, MockReply
from src.llm_providers.schemas import TokenUsage, ToolCall
from src.tools.registry import ToolRegistry
from src.tools.stubs import SearchServicesTool

pytestmark = [pytest.mark.agent, pytest.mark.unit]


def make_registry() -> ToolRegistry:
    """Registry with the single search stub used by golden scenarios."""
    registry = ToolRegistry()
    registry.register(SearchServicesTool())
    return registry


def make_agent(tenant_id: uuid.UUID) -> Agent:
    return Agent(
        tenant_id=tenant_id,
        name="Sales Assistant",
        system_prompt="Ты консультант салона «Красота».",
        welcome_message="Welcome",
        language="ru",
        max_context_messages=12,
        temperature=0.5,
        allowed_tools=["search_services", "search_faq", "save_lead", "handoff_request"],
        is_active=True,
    )


def _mock_result(value: Any = None) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = value
    scalars.all.return_value = value if isinstance(value, list) else []
    result.scalars.return_value = scalars
    return result


def make_session(agent: Agent) -> AsyncMock:
    """Mock session: idempotency→None, customer→None, conversation→None, agent→agent."""
    session = AsyncMock()
    session.add = MagicMock()
    session.execute.side_effect = [
        _mock_result(None),  # idempotency check
        _mock_result(None),  # customer lookup → new
        _mock_result(None),  # conversation lookup → new
        _mock_result(agent),  # agent lookup
        _mock_result([]),  # conversation history for the loop
    ]
    return session


def make_incoming(tenant_id: uuid.UUID, text: str = "Сколько стоит стрижка?") -> Any:
    from src.telegram_adapter.schemas import IncomingMessage, UserIdentity

    return IncomingMessage(
        tenant_id=tenant_id,
        bot_id=uuid.uuid4(),
        telegram_message_id=int.from_bytes(uuid.uuid4().bytes[:4], "little"),
        user=UserIdentity(
            telegram_user_id=int.from_bytes(uuid.uuid4().bytes[:4], "little") % 10**9,
            telegram_chat_id=int.from_bytes(uuid.uuid4().bytes[:4], "little") % 10**9,
            first_name="Anna",
        ),
        text=text,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_end_to_end_agent_turn_persists_reply_state_and_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сквозной ход: текст → LLM → ответ/состояние/usage сохранены в сессии."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    tenant_id = uuid.uuid4()
    agent = make_agent(tenant_id)
    session = make_session(agent)

    provider = MockLLMProvider(
        responses=["Здравствуйте! Стрижка доступна в каталоге."],
        usage=TokenUsage(prompt_tokens=17, completion_tokens=5, total_tokens=22),
    )
    loop = AgentLoop(provider, make_registry())
    use_case = ProcessIncomingMessageUseCase(session, agent_loop=loop)

    result = await use_case.execute(make_incoming(tenant_id))

    assert result.response_text == "Здравствуйте! Стрижка доступна в каталоге."
    session.commit.assert_awaited_once()

    added = [call.args[0] for call in session.add.call_args_list]
    messages = [obj for obj in added if isinstance(obj, Message)]
    agent_messages = [m for m in messages if m.role == MessageRole.AGENT]
    user_messages = [m for m in messages if m.role == MessageRole.USER]

    assert len(user_messages) == 1
    assert len(agent_messages) == 1
    agent_message = agent_messages[0]
    assert agent_message.ai_model == "mock-model"
    assert agent_message.meta is not None
    assert agent_message.meta["fallback"] is False
    assert agent_message.meta["llm_rounds"] == 1
    assert agent_message.meta["handoff_requested"] is False

    # Token usage recorded through TokenUsageService (same session, pre-commit).
    usage_rows = [obj for obj in added if isinstance(obj, TokenUsageRow)]
    assert len(usage_rows) == 1
    assert usage_rows[0].prompt_tokens == 17
    assert usage_rows[0].completion_tokens == 5
    assert usage_rows[0].model == "mock-model"

    # New conversation advanced: greeting → need_discovery after first reply.
    conversations = [obj for obj in added if isinstance(obj, Conversation)]
    assert conversations, "new conversation must be added"
    assert conversations[0].state == ConversationState.NEED_DISCOVERY


@pytest.mark.unit
@pytest.mark.asyncio
async def test_end_to_end_tool_call_scenario_records_meta_and_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Golden: вопрос о цене → search_services → CONSULTATION + tool-meta."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    tenant_id = uuid.uuid4()
    agent = make_agent(tenant_id)
    session = make_session(agent)

    provider = MockLLMProvider(
        [
            MockReply(
                tool_calls=[
                    ToolCall(id="call-1", name="search_services", arguments={"query": "стижка"})
                ]
            ),
            "Стрижку находим: цена по каталогу.",
        ]
    )
    loop = AgentLoop(provider, make_registry())
    use_case = ProcessIncomingMessageUseCase(session, agent_loop=loop)

    result = await use_case.execute(make_incoming(tenant_id))

    assert result.response_text == "Стрижку находим: цена по каталогу."
    added = [call.args[0] for call in session.add.call_args_list]
    agent_messages = [
        obj for obj in added if isinstance(obj, Message) and obj.role == MessageRole.AGENT
    ]
    assert agent_messages[0].meta["tools"] == [{"name": "search_services", "status": "ok"}]
    assert agent_messages[0].meta["llm_rounds"] == 2

    conversations = [obj for obj in added if isinstance(obj, Conversation)]
    assert conversations[0].state == ConversationState.CONSULTATION
