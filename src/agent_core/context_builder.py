"""Context assembly for agent turns (docs/ARCHITECTURE.md §9).

Message layout sent to the LLM (split rules):
1. system message = agent.system_prompt + policy block (policies.py) —
   leading message, tenant-owned text first;
2. optional `system` block with the conversation context_summary facts;
3. history window = last `agent.max_context_messages` DB messages with
   role user/agent (staff/system excluded from the LLM context);
4. tool results and synthetic loop messages are appended by agent_loop,
   not by this builder.
"""

from __future__ import annotations

from collections.abc import Sequence

from src.agent_core.policies import system_header
from src.db.models.agents import Agent
from src.db.models.enums import MessageRole
from src.db.models.messages import Message
from src.llm_providers.schemas import LLMMessage

_ROLE_TO_LLM_ROLE = {
    MessageRole.USER: "user",
    MessageRole.AGENT: "assistant",
}


def build_messages(
    agent: Agent,
    history: Sequence[Message],
    context_summary: str | None,
) -> list[LLMMessage]:
    """Compose the base message list for one agent turn."""
    messages = [
        LLMMessage(role="system", content=system_header(agent.system_prompt, agent.language))
    ]

    if context_summary and context_summary.strip():
        messages.append(
            LLMMessage(
                role="system",
                content=f"Сводка диалога (факты): {context_summary.strip()}",
            )
        )

    window = [
        message for message in history if message.role in (MessageRole.USER, MessageRole.AGENT)
    ][-agent.max_context_messages :]

    messages.extend(
        LLMMessage(role=_ROLE_TO_LLM_ROLE[message.role], content=message.text)
        for message in window
    )
    return messages
