"""Agent loop orchestration: one full dialog turn (docs/ARCHITECTURE.md §7, §11).

Per-turn flow:
1. If `conversation.handoff_active` — the LLM is NOT called at all (§13);
   the caller persists the incoming text as a user message for the staff.
2. Context is assembled (context_builder); tools are advertised from the
   registry whitelist of the agent; the LLM request uses the per-tenant
   model override (`agents.model`) and `agents.temperature`.
3. Tool calls are executed through `run_tool` (registry whitelist,
   Pydantic validation); results/errors go back to the LLM as `tool`
   messages; at most MAX_TOOL_CALLS_PER_TURN calls per turn.
4. `handoff_request` is intercepted BEFORE the registry: the backend sets
   `conversation.handoff_active=True` immediately, a synthetic
   confirmation is fed to the agent, and tools are locked off for the
   remaining rounds. Notifications are Stage 7 scope.
5. Budget overflow forces a final round WITHOUT tools; a tool response in
   that round degrades to a defined fallback.
6. The final text passes response_validator; on failure ONE regeneration
   without tools, then a defined fallback. Provider failures (timeout,
   rate limit, connection, degradation) map to polite fallbacks;
   LLMConfigurationError propagates (operator mistake).
7. Usage is recorded through TokenUsageService in the caller's session;
   the agent reply itself is persisted by the calling use case.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.agent_core import fallbacks as fallback_module
from src.agent_core import state_machine
from src.agent_core.context_builder import build_messages
from src.agent_core.response_validator import validate_response
from src.config.settings import get_settings
from src.db.context import TenantContext
from src.db.models.agents import Agent
from src.db.models.conversations import Conversation
from src.db.models.enums import ConversationState
from src.db.repositories.repositories import MessageRepository
from src.llm_providers.base import LLMProvider
from src.llm_providers.exceptions import (
    LLMConfigurationError,
    LLMProviderError,
)
from src.llm_providers.schemas import LLMMessage, LLMRequest, LLMResponse, ToolCall
from src.monitoring.logging import get_logger
from src.tools.handoff_request import STAFF_UNAVAILABLE, HandoffRequestTool
from src.tools.registry import MAX_TOOL_CALLS_PER_TURN, ToolRegistry, run_tool
from src.tools.schemas import ToolError, ToolResult

logger = get_logger("agent_core.agent_loop")

_HANDOFF_TOOL_NAME = "handoff_request"
_MAX_LLM_ROUNDS = 8
_TOOL_BUDGET_EXHAUSTED_NOTE = "Лимит действий за ход исчерпан; отвечай клиенту текстом."
_SYSTEM_ERROR_HANDOFF_NOTE = (
    "Не удалось обработать сообщение клиента: техническая ошибка модели. "
    "Сообщите клиенту, что запрос зафиксирован и оператор свяжется с ним."
)
_INTERCEPTED_HANDOFF_RESULT = {
    "status": "принято",
    "note": "Сотрудник подключается, ожидайте ответа.",
}
_TOOL_BUDGET_EXHAUSTED_NOTE = "Лимит действий за ход исчерпан; отвечай клиенту текстом."


@dataclass(frozen=True)
class AgentTurnResult:
    """Outcome of one agent turn for the calling use case."""

    response_text: str
    new_state: ConversationState
    ai_model: str
    llm_rounds: int
    tool_meta: list[dict[str, Any]] = field(default_factory=list)
    used_fallback: bool = False
    handoff_requested: bool = False
    handoff_active: bool = False

    def meta(self) -> dict[str, Any]:
        """`messages.meta` payload: tool metadata without PII or secrets.

        Arguments of tool calls are deliberately not stored (PII
        minimization, docs/SECURITY.md §8): only names, statuses and safe
        error texts.
        """
        return {
            "tools": self.tool_meta,
            "llm_rounds": self.llm_rounds,
            "handoff_requested": self.handoff_requested,
            "fallback": self.used_fallback,
        }


class AgentLoop:
    """Runs one dialog turn against an LLM provider and the tool registry."""

    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        *,
        max_llm_rounds: int = _MAX_LLM_ROUNDS,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self._max_llm_rounds = max_llm_rounds

    async def run(
        self,
        session: AsyncSession,
        tenant_context: TenantContext,
        agent: Agent,
        conversation: Conversation,
    ) -> AgentTurnResult:
        """Produce the agent reply for the current turn (user text already saved)."""
        current_state = conversation.state

        if conversation.handoff_active:
            # ARCHITECTURE §13: no LLM while the dialog belongs to the staff.
            return AgentTurnResult(
                response_text="",
                new_state=current_state,
                ai_model="handoff",
                llm_rounds=0,
                handoff_active=True,
            )

        message_repo = MessageRepository(session, tenant_context)
        history = await message_repo.get_conversation_history(
            conversation.id, limit=agent.max_context_messages
        )
        base_messages = build_messages(agent, history, conversation.context_summary)
        tool_specs = self.registry.get_specs(agent.allowed_tools)

        settings = get_settings()
        fallback_model = agent.model or settings.llm_default_model or "unknown"

        loop_messages: list[LLMMessage] = []
        tool_meta: list[dict[str, Any]] = []
        executed_tools: list[str] = []
        budget = MAX_TOOL_CALLS_PER_TURN
        handoff_lock = False
        overflow = False
        llm_rounds = 0
        handoff_requested = False
        regenerated_once = False

        while llm_rounds < self._max_llm_rounds:
            final_round = overflow or handoff_lock or budget <= 0
            request = LLMRequest(
                messages=list(base_messages) + list(loop_messages),
                tools=None if final_round else tool_specs,
                model=agent.model,
                temperature=float(agent.temperature),
                max_tokens=settings.llm_max_tokens,
                timeout=settings.llm_timeout,
            )
            try:
                response = await self.provider.complete(request)
            except LLMConfigurationError:
                raise
            except LLMProviderError as error:
                logger.warning(
                    "agent.turn.fallback",
                    error_type=type(error).__name__,
                    conversation_id=str(conversation.id),
                )
                fallback = self._fallback_result(
                    error,
                    current_state=current_state,
                    ai_model=fallback_model,
                    llm_rounds=llm_rounds,
                    tool_meta=tool_meta,
                    handoff_requested=handoff_requested,
                )
                # Stage 7.2: SYSTEM_ERROR handoff — the same backend procedure,
                # called directly (never through the LLM), only when the tenant
                # is configured for it and no handoff exists yet (docs/§13).
                if not conversation.handoff_active and not handoff_requested:
                    await _handoff_on_provider_error(
                        conversation_id=conversation.id,
                        tenant_context=tenant_context,
                        session=session,
                    )
                if (
                    conversation.handoff_active
                    and conversation.state == (ConversationState.HANDOFF)
                    and not handoff_requested
                ):
                    fallback = AgentTurnResult(
                        response_text=fallback.response_text,
                        new_state=ConversationState.HANDOFF,
                        ai_model=fallback.ai_model,
                        llm_rounds=fallback.llm_rounds,
                        tool_meta=fallback.tool_meta,
                        used_fallback=fallback.used_fallback,
                        handoff_requested=True,
                    )
                return fallback

            llm_rounds += 1
            await self._record_usage(session, tenant_context, response, conversation.id)

            if response.has_tool_calls and final_round:
                # Tool requests even after tools were removed: degrade politely.
                logger.warning(
                    "agent.turn.tool_overflow_unresolved", conversation_id=str(conversation.id)
                )
                return AgentTurnResult(
                    response_text=fallback_module.FALLBACK_TOOL_OVERFLOW,
                    new_state=state_machine.transition(
                        current_state, executed_tools, had_agent_reply=True
                    ),
                    ai_model=response.model,
                    llm_rounds=llm_rounds,
                    tool_meta=tool_meta,
                    used_fallback=True,
                    handoff_requested=handoff_requested,
                )

            if response.has_tool_calls:
                tool_calls = response.tool_calls or []
                loop_messages.append(
                    LLMMessage(role="assistant", content=response.content, tool_calls=tool_calls)
                )
                for call in tool_calls:
                    if budget <= 0:
                        overflow = True
                        budget_note = _TOOL_BUDGET_EXHAUSTED_NOTE
                    elif call.name == _HANDOFF_TOOL_NAME:
                        # Intercepted BEFORE the registry (§13): the REAL backend
                        # procedure performs row+flag; on failure the agent keeps
                        # working (the client is never told a human joins).
                        budget -= 1
                        handoff_note = str((call.arguments or {}).get("note") or "").replace(
                            "\n", " "
                        )[:500]
                        tool = HandoffRequestTool()
                        handoff_result = await tool.create_handoff(
                            conversation_id=conversation.id,
                            tenant_context=tenant_context,
                            session=session,
                            note=handoff_note,
                            trigger="agent_decision",
                        )
                        if handoff_result.ok:
                            handoff_requested = True
                            handoff_lock = True
                            executed_tools.append(call.name)
                            tool_meta.append(self._tool_meta_entry(call.name, handoff_result))
                            result_text = json.dumps(
                                (handoff_result.data or {"handoff_id": "", "status": "accepted"}),
                                ensure_ascii=False,
                            )
                        else:
                            tool_meta.append(self._tool_meta_entry(call.name, handoff_result))
                            result_text = handoff_result.error_message or STAFF_UNAVAILABLE
                        loop_messages.append(
                            LLMMessage(
                                role="tool",
                                content=result_text,
                                tool_call_id=self._call_id(call),
                            )
                        )
                        continue
                    else:
                        budget -= 1
                        try:
                            result = await run_tool(
                                self.registry,
                                call.name,
                                call.arguments,
                                allowed_tools=agent.allowed_tools,
                                tenant_context=tenant_context,
                                session=session,
                            )
                        except ToolError as error:
                            result = ToolResult(ok=False, error_message=error.message)
                        executed_tools.append(call.name)
                        tool_meta.append(self._tool_meta_entry(call.name, result))
                        budget_note = self._result_text(result)
                    loop_messages.append(
                        LLMMessage(
                            role="tool",
                            content=budget_note,
                            tool_call_id=self._call_id(call),
                        )
                    )
                continue

            # ─── Final text path ────────────────────────────────────────
            issues = validate_response(response.content, executed_tools)
            if not issues:
                return AgentTurnResult(
                    response_text=response.content,
                    new_state=state_machine.transition(
                        current_state, executed_tools, had_agent_reply=True
                    ),
                    ai_model=response.model,
                    llm_rounds=llm_rounds,
                    tool_meta=tool_meta,
                    used_fallback=False,
                    handoff_requested=handoff_requested,
                )

            if not regenerated_once:
                regenerated_once = True
                logger.warning(
                    "agent.turn.response_invalidated", conversation_id=str(conversation.id)
                )
                loop_messages.append(
                    LLMMessage(role="assistant", content=response.content or "[пустой ответ]")
                )
                loop_messages.append(
                    LLMMessage(
                        role="user",
                        content=(
                            "Техническая проверка: предыдущий ответ непригоден "
                            "(пустой, слишком длинный, со служебными артефактами или "
                            "неподтверждёнными заявлениями). Дай клиенту нормальный "
                            "короткий вежливый ответ. Не утверждай, что заявка "
                            "сохранена или клиент передан сотруднику, если фактического "
                            "действия не было."
                        ),
                    )
                )
                handoff_lock = True  # regeneration must not carry tools
                continue

            return AgentTurnResult(
                response_text=fallback_module.FALLBACK_RESPONSE_INVALID,
                new_state=state_machine.transition(
                    current_state, executed_tools, had_agent_reply=True
                ),
                ai_model=response.model,
                llm_rounds=llm_rounds,
                tool_meta=tool_meta,
                used_fallback=True,
                handoff_requested=handoff_requested,
            )

        # Round budget exhausted without a valid final text.
        return AgentTurnResult(
            response_text=fallback_module.FALLBACK_TOOL_OVERFLOW,
            new_state=state_machine.transition(
                current_state, executed_tools, had_agent_reply=True
            ),
            ai_model=fallback_model,
            llm_rounds=llm_rounds,
            tool_meta=tool_meta,
            used_fallback=True,
            handoff_requested=handoff_requested,
        )

    # ─── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _call_id(call: ToolCall) -> str:
        """Vendor id or a deterministic synthetic one for id-less providers."""
        return call.id or f"{call.name}:turn-call"

    @staticmethod
    def _result_text(result: ToolResult) -> str:
        if result.ok:
            return json.dumps(result.data or {"ok": True}, ensure_ascii=False)
        return (result.error_message or "Инструмент временно недоступен.").replace("\n", " ")

    @staticmethod
    def _tool_meta_entry(name: str, result: ToolResult) -> dict[str, Any]:
        entry: dict[str, Any] = {"name": name, "status": "ok" if result.ok else "error"}
        if not result.ok:
            entry["error_message"] = (result.error_message or "").replace("\n", " ")
        return entry

    def _fallback_result(
        self,
        error: LLMProviderError,
        *,
        current_state: ConversationState,
        ai_model: str,
        llm_rounds: int,
        tool_meta: list[dict[str, Any]],
        handoff_requested: bool,
    ) -> AgentTurnResult:
        return AgentTurnResult(
            response_text=fallback_module.fallback_for(error),
            new_state=state_machine.transition(current_state, [], had_agent_reply=True),
            ai_model=ai_model,
            llm_rounds=llm_rounds,
            tool_meta=tool_meta,
            used_fallback=True,
            handoff_requested=handoff_requested,
        )

    async def _record_usage(
        self,
        session: AsyncSession,
        tenant_context: TenantContext,
        response: LLMResponse,
        conversation_id: UUID,
    ) -> None:
        """Persist usage stats; accounting failures must never break the dialog."""
        try:
            from src.application.llm_usage_service import TokenUsageService

            await TokenUsageService(session).record(
                tenant_context, response, conversation_id=conversation_id
            )
        except Exception as error:  # noqa: BLE001 - accounting is best-effort
            logger.warning("agent.usage.record_failed", error_type=type(error).__name__)


async def _handoff_on_provider_error(
    *,
    conversation_id: UUID,
    tenant_context: TenantContext,
    session: AsyncSession,
) -> None:
    """SYSTEM_ERROR handoff after a provider fallback (Stage 7.2, task 6).

    Direct backend procedure (never through the LLM): internally the tool
    decides whether the tenant has `staff_chat_id`; skips/failures are
    silent warnings — the client already received the fallback text.
    """
    tool = HandoffRequestTool()
    result = await tool.create_handoff(
        conversation_id=conversation_id,
        tenant_context=tenant_context,
        session=session,
        note=_SYSTEM_ERROR_HANDOFF_NOTE,
        trigger="system_error",
    )
    logger.info(
        "agent.turn.system_error_handoff",
        accepted=result.ok,
        error_message=None if result.ok else (result.error_message or "")[:80],
        conversation_id=str(conversation_id),
    )
