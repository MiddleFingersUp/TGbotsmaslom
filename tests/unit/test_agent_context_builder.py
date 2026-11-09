"""Unit tests for context assembly (docs/ARCHITECTURE.md §9 split rules)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from src.agent_core.context_builder import build_messages
from src.agent_core.policies import POLICIES_BLOCK, system_header
from src.db.models.agents import Agent
from src.db.models.enums import MessageRole
from src.db.models.messages import Message


def _agent(max_context_messages: int = 4) -> Agent:
    return Agent(
        tenant_id=uuid.uuid4(),
        name="Sales Assistant",
        system_prompt="Ты консультант салона «Красота».",
        welcome_message="Hi",
        language="ru",
        max_context_messages=max_context_messages,
        temperature=0.5,
        allowed_tools=["search_services"],
    )


def _message(role: MessageRole, text: str) -> Message:
    return Message(
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        role=role,
        text=text,
        created_at=datetime.now(UTC),
    )


@pytest.mark.unit
class TestContextBuilder:
    """Split rules: system prompt + policy, summary block, bounded window."""

    def test_system_message_contains_prompt_and_policy(self) -> None:
        agent = _agent()
        messages = build_messages(agent, [], None)

        assert messages[0].role == "system"
        assert "Ты консультант салона «Красота»." in messages[0].content
        assert POLICIES_BLOCK in messages[0].content

    def test_history_window_bounded_by_max_context(self) -> None:
        agent = _agent(max_context_messages=2)  # after summary window stays 2
        history = [_message(MessageRole.USER, f"сообщение {i}") for i in range(10)]
        messages = build_messages(agent, history, None)

        content_texts = [m.content for m in messages if m.role == "user"]
        assert content_texts == ["сообщение 8", "сообщение 9"]  # last 2 kept

    def test_staff_and_system_roles_excluded_from_window(self) -> None:
        agent = _agent(max_context_messages=10)
        history = [
            _message(MessageRole.USER, "вопрос"),
            _message(MessageRole.STAFF, "ответ сотрудника"),
            _message(MessageRole.SYSTEM, "техническое"),
            _message(MessageRole.AGENT, "ответ"),
        ]
        messages = build_messages(agent, history, None)

        roles = [(m.role, m.content) for m in messages if m.role != "system"]
        assert roles == [("user", "вопрос"), ("assistant", "ответ")]

    def test_context_summary_added_as_system_block(self) -> None:
        agent = _agent()
        messages = build_messages(agent, [], "Ищет стрижку; интересна камуфляжная седина.")

        system_blocks = [m.content for m in messages if m.role == "system"]
        assert len(system_blocks) == 2
        assert "Сводка диалога (факты)" in system_blocks[1]
        assert "камуфляжная седина" in system_blocks[1]

    def test_empty_summary_omitted(self) -> None:
        agent = _agent()
        messages = build_messages(agent, [], "   ")
        assert sum(1 for m in messages if m.role == "system") == 1

    def test_role_mapping_user_agent(self) -> None:
        agent = _agent()
        history = [_message(MessageRole.USER, "Q"), _message(MessageRole.AGENT, "A")]
        messages = build_messages(agent, history, None)
        assert [(m.role, m.content) for m in messages[1:]] == [("user", "Q"), ("assistant", "A")]

    def test_language_rule_appended(self) -> None:
        header = system_header("Правила.", "ru")
        assert "Язык общения: русский" in header
