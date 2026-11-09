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
    """Create (or reset) the fixture tenant + active agent; return (tenant_id, agent_id)."""
    from src.config.settings import get_settings
    from src.db.models.agents import Agent
    from src.db.models.services import Service
    from src.db.models.tenants import Tenant
    from src.db.session import get_session_factory

    get_settings()  # refresh settings snapshot from env (LLM_*, DATABASE_URL)
    tenant_id = _fixed_uuid("tenant:" + (agent_model or "default"))
    agent_id = _fixed_uuid("agent:" + (agent_model or "default"))

    factory = get_session_factory()
    async with factory() as session, session.begin():
        tenant = await session.get(Tenant, tenant_id)
        if tenant is None:
            tenant = Tenant(
                id=tenant_id,
                name=f"E2E Smoke fixture ({agent_model or 'default-model'})",
            )
            session.add(tenant)
            await session.flush()

        # Idempotency: reset this tenant's agent/service rows; keep tenant.
        await session.execute(delete(Agent).where(Agent.tenant_id == tenant_id))
        await session.execute(delete(Service).where(Service.tenant_id == tenant_id))

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
    """Two dialog turns via the REAL use case + AgentLoop; report dicts only."""
    from src.agent_core.agent_loop import AgentLoop
    from src.application.process_incoming_message import ProcessIncomingMessageUseCase
    from src.config.settings import get_settings
    from src.db.models.agents import Agent as AgentModel
    from src.db.models.conversations import Conversation
    from src.db.models.customers import Customer
    from src.db.models.messages import Message
    from src.db.models.tenants import Tenant
    from src.db.models.token_usage import TokenUsage as TokenUsageRow
    from src.db.repositories.repositories import MessageRepository
    from src.db.session import get_session_factory
    from src.llm_providers.factory import LLMProviderFactory
    from src.telegram_adapter.schemas import IncomingMessage, UserIdentity
    from src.tools.stubs import build_default_stub_registry

    settings = get_settings()
    provider = LLMProviderFactory.from_settings(settings)
    loop = AgentLoop(provider, build_default_stub_registry())

    inputs = ["Привет!", "Сколько стоит стрижка женская?"]
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

            _ = MessageRepository  # imported for contract clarity (history via use case)
            report.append(
                {
                    "turn": index + 1,
                    "input": text,
                    "response": result.response_text,
                    "state": str(conversation.state) if conversation else "?",
                    "handoff_active": bool(conversation.handoff_active) if conversation else "?",
                    "agent_meta": [m.meta for m in agent_rows if m.meta],
                    "agent_ai_models": [m.ai_model for m in agent_rows if m.ai_model],
                    "usage_rows_tenant_total": len(usage_rows),
                    "usage_total_admin": usage_total,
                }
            )
    return report


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

    db_ok = await _preflight_db()
    if not db_ok:
        _prints("-> PostgreSQL недоступен. Выполни: docker compose up -d postgres")
        _prints("   затем: alembic upgrade head. e2e отменён (exit 2).")
        return 2
    if not _preflight_llm():
        _prints(f"-> llama-server недоступен по {LLM_BASE_URL} (модель {LLM_MODEL}).")
        _prints("   Загрузи модель и повтори. e2e отменён (exit 3).")
        return 3
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
