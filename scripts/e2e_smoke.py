"""E2E smoke for the full dialog contour against a REAL LLM and REAL PostgreSQL.

Manual run (NOT part of pytest; docs/DEVELOPMENT_PLAN.md Stage 5, iteration 5.3):

1. docker compose up -d postgres
2. alembic upgrade head
3. llama-server on http://127.0.0.1:1234/v1 (model qwen3.5-9b loaded)
4. .env with: LLM_PROVIDER=openai | LLM_API_BASE_URL=http://127.0.0.1:1234/v1
   | LLM_API_KEY=local-dev-key (fake local value) | LLM_DEFAULT_MODEL=qwen3.5-9b
   | LLM_TIMEOUT=120 (cold model)
5. .venv\\Scripts\\python.exe scripts\\e2e_smoke.py

Honest scope (fixed by Stage 5.1):
- the agent tools are Stage 5.1 STUBS (search_services/search_faq/save_lead/
  handoff_request): they do NOT read tenant's services from PostgreSQL.
  Demo services are seeded for later stages only.
- no Telegram adapter, no notifications (Stage 7), no real handoff row.

Exit codes: 0 = full run ok; 2 = PostgreSQL preflight failed (prints what to
start); 3 = llama-server preflight failed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

stdout: Any = sys.stdout
stderr: Any = sys.stderr
stdout.reconfigure(encoding="utf-8", errors="replace")
stderr.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import delete, select  # noqa: E402

# ─────────────────────────── config / fixtures ───────────────────────────

_E2E_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "tgbots-e2e-smoke")

LLM_BASE_URL = "http://127.0.0.1:1234/v1"
LLM_FAKE_KEY = "local-dev-key"  # fake value for the local server; NEVER a real secret
LLM_MODEL = "qwen3.5-9b"
LLM_TIMEOUT = 120.0  # cold-model friendly (docs/DEVELOPMENT_PLAN.md Stage 5.3)

AGENT_SYSTEM_PROMPT = (
    "Ты вежливый консультант салона красоты «Э2Э Тест». Отвечай на русском. "
    "Про цены и услуги говори только по данным поиска по каталогу. "
    "Не придумывай ничего от себя."
)

DEMO_SERVICES = [
    {
        "name": "Стрижка женская",
        "description": "Модельная стрижка с мытьём головы",
        "price_min": 1500,
    },
    {
        "name": "Окрашивание",
        "description": "Полное окрашивание в один тон",
        "price_min": 4000,
    },
    {
        "name": "Маникюр",
        "description": "Классический маникюр с покрытием",
        "price_min": 2000,
    },
]


def _fixed_uuid(tag: str) -> UUID:
    """Deterministic fixture id (safe to re-run: rows are reset, not duplicated)."""
    return uuid.uuid5(_E2E_NAMESPACE, tag)


def _prints(*values: Any) -> None:
    print(*values)


# ─────────────────────────── preflight ───────────────────────────


async def _preflight_db() -> bool:
    """Check PostgreSQL reachability (docker compose service)."""
    try:
        from src.db.session import get_engine

        engine = get_engine()
        async with engine.connect() as conn:
            await conn.exec_driver_sql("SELECT 1")
        return True
    except Exception as error:
        _prints("PREFLIGHT DB FAILED:", type(error).__name__, str(error)[:200])
        return False


def _preflight_llm() -> bool:
    """Check llama-server reachability and that the model is loaded."""
    try:
        import httpx

        response = httpx.get(f"{LLM_BASE_URL}/models", timeout=5)
        models = [m.get("id") for m in response.json().get("data", [])]
        if LLM_MODEL not in models:
            _prints(f"PREFLIGHT LLM: model '{LLM_MODEL}' not loaded; available: {models}")
            return False
        return True
    except Exception as error:
        _prints("PREFLIGHT LLM FAILED:", type(error).__name__, str(error)[:200])
        return False


# ─────────────────────────── fixtures ───────────────────────────


async def seed_fixture(agent_model: str | None) -> tuple[UUID, UUID]:
    """Create (or reset) the fixture tenant + active agent; return (tenant_id, agent_id).

    Re-runs are fully idempotent: previous dialog facts (token_usage,
    bookings, handoffs, leads, conversations+messages, customers) are
    deleted in FK-safe order, then the tenant is recreated fresh.
    """
    from src.config.settings import get_settings
    from src.db.models.agents import Agent
    from src.db.models.bookings import Booking
    from src.db.models.conversations import Conversation
    from src.db.models.customers import Customer
    from src.db.models.handoffs import Handoff
    from src.db.models.leads import Lead
    from src.db.models.services import Service
    from src.db.models.tenants import Tenant
    from src.db.models.token_usage import TokenUsage
    from src.db.session import get_session_factory

    get_settings()  # refresh settings snapshot from env (LLM_*, DATABASE_URL)
    tenant_id = _fixed_uuid("tenant:" + (agent_model or "default"))
    agent_id = _fixed_uuid("agent:" + (agent_model or "default"))

    factory = get_session_factory()
    async with factory() as session, session.begin():
        # FK-safe purge of the previous run for this fixture tenant
        for delete_stmt in (
            delete(TokenUsage).where(TokenUsage.tenant_id == tenant_id),
            delete(Booking).where(Booking.tenant_id == tenant_id),
            delete(Handoff).where(Handoff.tenant_id == tenant_id),
            delete(Lead).where(Lead.tenant_id == tenant_id),
            delete(Conversation).where(Conversation.tenant_id == tenant_id),
            delete(Customer).where(Customer.tenant_id == tenant_id),
            delete(Service).where(Service.tenant_id == tenant_id),
            delete(Agent).where(Agent.tenant_id == tenant_id),
        ):
            await session.execute(delete_stmt)
        await session.execute(delete(Tenant).where(Tenant.id == tenant_id))

        tenant = Tenant(
            id=tenant_id,
            name=f"E2E Smoke fixture ({agent_model or 'default-model'})",
        )
        session.add(tenant)
        await session.flush()

        agent = Agent(
            id=agent_id,
            tenant_id=tenant_id,
            name=f"E2E agent ({agent_model or 'default'})",
            system_prompt=AGENT_SYSTEM_PROMPT,
            language="ru",
            model=agent_model,
            temperature=0.5,
            max_context_messages=12,
            allowed_tools=["search_services"],
            welcome_message="Здравствуйте! Чем могу помочь?",
            is_active=True,
        )
        session.add(agent)

        for index, svc in enumerate(DEMO_SERVICES):
            session.add(
                Service(
                    id=_fixed_uuid(f"service:{agent_model or 'default'}:{index}"),
                    tenant_id=tenant_id,
                    name=svc["name"],
                    description=svc["description"],
                    price_min=svc["price_min"],
                    currency="RUB",
                    tags=["demo"],
                )
            )
    return tenant_id, agent_id


# ─────────────────────────── the run ───────────────────────────


async def run_two_turns(tenant_id: UUID, agent_id: UUID) -> list[dict[str, Any]]:
    """Three dialog turns via the REAL use case + AgentLoop; report dicts only.

    Stage 6.2: the loop uses the PRODUCTION registry (real retrieval tools).
    Expected story:
    1. 'Привет!'                          → no tools;
    2. 'Сколько стоит стрижка женская?'   → search_services → price from the
       SEEDED catalog ('1500.00');
    3. 'Полёт на марс?'                   → honest refusal (results=[] or a
       polite no-data text, never an invented price).
    """
    import re

    from src.agent_core.agent_loop import AgentLoop
    from src.application.process_incoming_message import (
        ProcessIncomingMessageUseCase,
        build_default_registry,
    )
    from src.config.settings import get_settings
    from src.db.models.agents import Agent as AgentModel
    from src.db.models.conversations import Conversation
    from src.db.models.customers import Customer
    from src.db.models.messages import Message
    from src.db.models.tenants import Tenant
    from src.db.models.token_usage import TokenUsage as TokenUsageRow
    from src.db.session import get_session_factory
    from src.llm_providers.factory import LLMProviderFactory
    from src.telegram_adapter.schemas import IncomingMessage, UserIdentity

    settings = get_settings()
    provider = LLMProviderFactory.from_settings(settings)
    loop = AgentLoop(provider, build_default_registry())

    inputs = ["Привет!", "Сколько стоит стрижка женская?", "Полёт на марс?"]
    expected_price_token = "1500"  # seeded fixture value for Стрижка женская
    report: list[dict[str, Any]] = []

    factory = get_session_factory()
    for index, text in enumerate(inputs):
        async with factory() as session:
            tenant = await session.get(Tenant, tenant_id)
            agent = await session.get(AgentModel, agent_id)
            assert tenant is not None and agent is not None

            customer = Customer(
                tenant_id=tenant_id,
                telegram_user_id=7700_000_100 + index,
                telegram_chat_id=7700_000_100 + index,
                first_name="E2E",
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
            )
            session.add(customer)
            await session.flush()

            incoming = IncomingMessage(
                tenant_id=tenant_id,
                bot_id=_fixed_uuid("fake-bot"),
                telegram_message_id=9000 + index,
                user=UserIdentity(
                    telegram_user_id=customer.telegram_user_id,
                    telegram_chat_id=customer.telegram_chat_id,
                    first_name="E2E",
                ),
                text=text,
            )

            use_case = ProcessIncomingMessageUseCase(session, agent_loop=loop)
            result = await use_case.execute(incoming)

            conversation = await session.get(Conversation, result.conversation_id)
            history = (
                (
                    await session.execute(
                        select(Message).where(
                            Message.tenant_id == tenant_id,
                            Message.conversation_id == result.conversation_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            usage_rows = (
                (
                    await session.execute(
                        select(TokenUsageRow).where(TokenUsageRow.tenant_id == tenant_id)
                    )
                )
                .scalars()
                .all()
            )

            agent_rows = [m for m in history if m.role.value == "agent"]
            usage_total = sum(row.prompt_tokens + row.completion_tokens for row in usage_rows)

            price_tokens_found = (
                re.findall(rf"\b{expected_price_token}(?:[.,]\d{{2}})?\b", result.response_text)
                if "стоимость" in text or "стоит" in text
                else []
            )
            tool_names = [
                entry.get("name")
                for meta in (m.meta for m in agent_rows if m.meta)
                for entry in meta.get("tools", [])
            ]

            report.append(
                {
                    "turn": index + 1,
                    "input": text,
                    "response": result.response_text,
                    "state": str(conversation.state) if conversation else "?",
                    "handoff_active": bool(conversation.handoff_active) if conversation else "?",
                    "agent_meta": [m.meta for m in agent_rows if m.meta],
                    "agent_ai_models": [m.ai_model for m in agent_rows if m.ai_model],
                    "tools_used": tool_names,
                    "price_tokens": price_tokens_found,
                    "usage_rows_tenant_total": len(usage_rows),
                    "usage_total_admin": usage_total,
                }
            )
    return report


async def run_llm_only_mode() -> bool:
    """Live LLM contour without PostgreSQL: AgentLoop + stub tools + mock session.

    Checks (Stage 5.3, «проверить не удаётся» list): greeting question must
    not trigger tools; price question must call search_services with valid
    arguments; answer must only carry the stub's price text; two consecutive
    turns must be stable. Persistence is NOT proven here (no DB).
    """
    from unittest.mock import AsyncMock, MagicMock

    import httpx

    from src.agent_core import state_machine as fsm
    from src.agent_core.agent_loop import AgentLoop
    from src.config.settings import get_settings
    from src.db.context import TenantContext
    from src.db.models.agents import Agent
    from src.db.models.conversations import Conversation
    from src.db.models.enums import ConversationState
    from src.llm_providers.factory import LLMProviderFactory
    from src.tools.stubs import build_default_stub_registry

    settings = get_settings()
    provider = LLMProviderFactory.from_settings(settings)

    async def _log_error_response(response: httpx.Response) -> None:
        """E2E diagnostics: non-2xx responses from llama-server (no secrets)."""
        if response.status_code >= 300:
            await response.aread()
            _prints(
                f"HTTP {response.status_code}: "
                f"{response.text[:400].encode('ascii', 'replace').decode()}"
            )

    provider.diagnostic_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        event_hooks={"response": [_log_error_response]}
    )
    provider._client = provider.diagnostic_client  # type: ignore[attr-defined] # noqa: SLF001
    provider._owns_client = True  # type: ignore[attr-defined] # noqa: SLF001

    loop = AgentLoop(provider, build_default_stub_registry())

    def _mock_session(history_text: str | None = None) -> Any:
        """Mock history = только текущее user-сообщение (как в production-пути,
        где user text persisted до вызова цикла)."""
        from datetime import UTC, datetime

        from src.db.models.enums import MessageRole
        from src.db.models.messages import Message

        session = AsyncMock()
        result = MagicMock()
        scalars = MagicMock()
        history: list[Any] = []
        if history_text:
            history.append(
                Message(
                    tenant_id=uuid.uuid5(_E2E_NAMESPACE, "llm-only-tenant"),
                    conversation_id=uuid.uuid5(_E2E_NAMESPACE, "llm-only-conv"),
                    role=MessageRole.USER,
                    text=history_text,
                    created_at=datetime.now(UTC),
                )
            )
        scalars.all.return_value = history
        scalars.first.return_value = None
        result.scalars.return_value = scalars
        session.execute.return_value = result
        return session

    successes = True

    for key, agent_model in (("live-default", None), ("live-override", LLM_MODEL)):
        agent = Agent(
            tenant_id=uuid.uuid5(_E2E_NAMESPACE, "llm-only-tenant:" + key),
            name=f"E2E live agent ({agent_model or 'default'})",
            system_prompt=AGENT_SYSTEM_PROMPT,
            language="ru",
            model=agent_model,
            temperature=0.3,
            max_context_messages=12,
            allowed_tools=["search_services"],
            welcome_message="Здравствуйте!",
            is_active=True,
        )
        tenant_context = TenantContext(tenant_id=agent.tenant_id)
        _prints("-" * 72)
        _prints(f"LLM-ONLY FIXTURE: {key} (agent.model={agent_model!r})")

        turns_config = [
            ("greeting", "Привет!", ConversationState.GREETING),
            ("price", "Сколько стоит стрижка женская?", ConversationState.NEED_DISCOVERY),
        ]
        for label, text, start_state in turns_config:
            conversation = Conversation(
                tenant_id=agent.tenant_id,
                customer_id=uuid.uuid5(_E2E_NAMESPACE, "llm-only-customer:" + label),
                state=start_state,
                handoff_active=False,
                message_count=2,
            )
            result = await loop.run(
                _mock_session(history_text=text), tenant_context, agent, conversation
            )
            tool_names = [entry["name"] for entry in result.tool_meta]
            _prints("-" * 72)
            _prints(f"TURN ({label}) input: {text}")
            _prints(f"  REQUEST_MODEL: agent.model={agent.model!r} → ai_model={result.ai_model}")
            _prints(f"  ANSWER: {result.response_text[:400]}")
            _prints(f"  TOOLS: {json.dumps(result.tool_meta, ensure_ascii=False)}")
            _prints(f"  STATE: {start_state.value} -> {result.new_state.value}")
            _prints(f"  FALLBACK: {result.used_fallback} | llm_rounds: {result.llm_rounds}")

            if label == "greeting" and tool_names:
                _prints("  CHECK FAILED: приветствие вызвало инструмент")
                successes = False
            if label == "price":
                if "search_services" not in tool_names:
                    _prints("  CHECK FAILED: ценовой вопрос не вызвал search_services")
                    successes = False
                if result.new_state != fsm.transition(
                    start_state, tool_names, had_agent_reply=True
                ):
                    _prints("  CHECK FAILED: state не соответствует таблице переходов")
                    successes = False

        # Honest answer check: the price text can only be the stub's («по каталогу»)
        # никаких выдуманных чисел от модели не должно проходить по конструкции.
    _prints("=" * 72)
    _prints("LLM-ONLY SMOKE FINISHED; persistence NOT proven (no PostgreSQL in env).")
    return successes


async def main() -> int:
    """Preflight, seed fixtures, run two turns on both tenant configurations."""
    _prints("=" * 72)
    _prints("E2E SMOKE — TGbots Agent Core (Stage 5.3)")
    _prints("=" * 72)

    # Force the local-model configuration before Settings snapshot (no cloud).
    os.environ["LLM_PROVIDER"] = "openai"
    os.environ["LLM_API_BASE_URL"] = LLM_BASE_URL
    os.environ["LLM_API_KEY"] = LLM_FAKE_KEY
    os.environ["LLM_DEFAULT_MODEL"] = LLM_MODEL
    os.environ.setdefault("LLM_TIMEOUT", str(int(LLM_TIMEOUT)))
    os.environ["LLM_MAX_RETRIES"] = "1"
    os.environ["LLM_MAX_TOKENS"] = "512"

    from src.config.settings import get_settings

    get_settings.cache_clear()

    if not _preflight_llm():
        _prints(f"-> llama-server недоступен по {LLM_BASE_URL} (модель {LLM_MODEL}).")
        _prints("   Загрузи модель и повтори. e2e отменён (exit 3).")
        return 3

    skip_db = "--skip-db" in sys.argv
    if not skip_db:
        db_ok = await _preflight_db()
        if not db_ok:
            _prints("-> PostgreSQL недоступен. Выполни: docker compose up -d postgres")
            _prints("   затем: alembic upgrade head. e2e отменён (exit 2).")
            _prints("   Для проверки ТОЛЬКО живой LLM-линии используй: e2e_smoke.py --skip-db")
            return 2

    if skip_db:
        # LLM-only live mode: full AgentLoop with the real model against a Mock
        # session (no persistence). Verification for the case when PostgreSQL
        # can't be started locally. Честная граница: messages/token_usage НЕ
        # пишутся в БД; state вычисляется той же state_machine.
        ok = await run_llm_only_mode()
        return 0 if ok else 3

    _prints("PREFLIGHT OK: PostgreSQL и llama-server доступны.")

    for key in ("e2e-default", "e2e-override"):
        agent_model = None if key == "e2e-default" else LLM_MODEL
        _prints("-" * 72)
        _prints(f"FIXTURE: {key} (agent.model={agent_model!r})")
        tenant_id, agent_id = await seed_fixture(agent_model)
        turns = await run_two_turns(tenant_id, agent_id)
        for turn in turns:
            _prints("-" * 72)
            _prints(f"TURN {turn['turn']} → input: {turn['input']}")
            _prints(f"  ANSWER: {turn['response'][:300]}")
            _prints(f"  STATE: {turn['state']} | handoff_active: {turn['handoff_active']}")
            _prints(f"  AI_MODELS: {turn['agent_ai_models']}")
            _prints(f"  TOOLS_USED: {turn['tools_used']}")
            if turn["price_tokens"]:
                _prints(f"  PRICE_FROM_CATALOG: token {turn['price_tokens']} найден в ответе")
            _prints(f"  TOOL_META: {json.dumps(turn['agent_meta'], ensure_ascii=False)}")
            _prints(
                f"  TOKEN_USAGE: rows(this tenant)={turn['usage_rows_tenant_total']} "
                f"total_tokens={turn['usage_total_admin']}"
            )

    _prints("=" * 72)
    _prints("E2E SMOKE SUCCESS.")
    _prints("Границы честности: инструменты — заглушки Stage 5.1 (БД-каталог читают")
    _prints("только этапы 6-7); Telegram/handoff-уведомления вне scope.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
