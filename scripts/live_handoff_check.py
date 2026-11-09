"""Manual live check: full handoff mechanics against a REAL Telegram group.

Stage 7.2 acceptance script (run BY HAND, never in pytest — getUpdates is
forbidden in production code). Reads secrets from `.env`:
- TELEGRAM_BOT_TOKEN (single-bot MVP)
- TEST_STAFF_CHAT_ID (staff group where the bot is a member)

Mechanics (docs/ARCHITECTURE.md §13, PLAN-OD-5):
- Phase A: agent turn «Хочу поговорить с оператором» → real handoff tool →
  handoffs row + staff notification into the group (staff_message_id stored);
- Phase B (up to 120 s): staff answers by REPLYING to the notification;
  '/back' → ProcessStaffReplyUseCase returns the dialog to AI mode
  (HANDOFF LIVE OK); a plain reply is relayed to the fixture client.

Honest boundaries: the fixture client chat IS the staff group (marked
«[клиент-фактура]»), so client delivery is verified by seeing the
staff-relayed text arrive in the same group. No secrets are printed.

Exit codes: 0 = HANDOFF LIVE OK; 2 = preflight/config failed;
3 = mechanics failed (reason printed).
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

import aiohttp  # noqa: E402
from sqlalchemy import select  # noqa: E221, E402

from src.agent_core.agent_loop import AgentLoop  # noqa: E402
from src.application.process_incoming_message import (  # noqa: E402
    ProcessIncomingMessageUseCase,
    build_default_registry,
)
from src.config.settings import build_default_bot, get_settings  # noqa: E402
from src.db.models.conversations import Conversation  # noqa: E221, E402
from src.db.models.customers import Customer  # noqa: E402
from src.db.models.handoffs import Handoff  # noqa: E402
from src.db.models.messages import Message  # noqa: E402
from src.db.models.tenants import Tenant  # noqa: E402
from src.db.session import get_session_factory  # noqa: E402
from src.handoff.use_case import ProcessStaffReplyUseCase  # noqa: E402
from src.llm_providers.factory import LLMProviderFactory  # noqa: E402
from src.telegram_adapter.schemas import IncomingMessage, UserIdentity  # noqa: E402

_E2E_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "tgbots-live-handoff")

AGENT_SYSTEM_PROMPT = (
    "Ты вежливый консультант салона «Лайв Хендофф Тест». Отвечай на русском. "
    "Если клиент просит живого оператора — используй инструмент передачи "
    "сотруднику (handoff). Факты — только из инструментов."
)

_WAIT_SECONDS = int(os.environ.get("LIVE_HANDOFF_WAIT", "120"))
_POLL_INTERVAL = 2.0

TELEGRAM_API = "https://api.telegram.org"


def _print(*values: Any) -> None:
    """Console output WITHOUT secrets (tokens are never printed)."""
    print(*values)  # noqa: T201 - manual check script


def _fixed_uuid(tag: str) -> uuid.UUID:
    return uuid.uuid5(_E2E_NAMESPACE, tag)


def _load_env() -> None:
    """Load .env once; token values stay in the process env only."""
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

    # Live-модель: локальный llama-server (как в Stage 5.3 E2E). Значения из
    # .env/окружения имеют приоритет; токены Telegram никогда не печатаются.
    os.environ.setdefault("LLM_PROVIDER", "openai")
    os.environ.setdefault("LLM_API_BASE_URL", "http://127.0.0.1:1234/v1")
    os.environ.setdefault("LLM_API_KEY", "local-dev-key")  # fake local value
    os.environ.setdefault("LLM_DEFAULT_MODEL", "qwen3.5-9b")
    os.environ.setdefault("LLM_TIMEOUT", "120")
    os.environ.setdefault("LLM_MAX_RETRIES", "1")
    os.environ.setdefault("LLM_MAX_TOKENS", "512")
    os.environ.setdefault("APP_ENV", "test")


def _preflight() -> tuple[Any, int | None, str]:
    """Fail-closed checks; returns (bot, staff_chat_id, token) or prints why."""
    _load_env()
    get_settings.cache_clear()
    settings = get_settings()

    bot = build_default_bot()
    if bot is None:
        _print("PREFLIGHT FAIL: TELEGRAM_BOT_TOKEN не задан в .env.")
        return None, None, ""

    raw = os.environ.get("TEST_STAFF_CHAT_ID", "")
    try:
        staff_chat_id = int(raw)
    except ValueError:
        _print("PREFLIGHT FAIL: TEST_STAFF_CHAT_ID не задан/не число.")
        return None, None, ""
    return bot, staff_chat_id, settings.telegram_bot_token or ""


async def seed_fixture(staff_chat_id: int) -> tuple[uuid.UUID, uuid.UUID]:
    """Create/reset the fixture tenant (staff chat) + fake client customer."""
    tenant_id = _fixed_uuid("tenant:live-handoff")
    agent_id = _fixed_uuid("agent:live-handoff")
    customer_id = _fixed_uuid("customer:live-handoff")

    factory = get_session_factory()
    async with factory() as session, session.begin():
        # Идемпотентность повторов: сбрасываем прошлые факты fixture (FK-порядок:
        # handoffs → conversations (messages — CASCADE)).
        handoff_rows = (
            (await session.execute(select(Handoff).where(Handoff.tenant_id == tenant_id)))
            .scalars()
            .all()
        )
        for handoff_row in handoff_rows:
            await session.delete(handoff_row)
        conv_rows = (
            (
                await session.execute(
                    select(Conversation).where(Conversation.tenant_id == tenant_id)
                )
            )
            .scalars()
            .all()
        )
        for conv in conv_rows:  # messages удаляются каскадом (ON DELETE CASCADE)
            await session.delete(conv)
        await session.flush()

        tenant = await session.get(Tenant, tenant_id)
        if tenant is None:
            session.add(
                Tenant(
                    id=tenant_id,
                    name="Live handoff fixture (не прод)",
                    staff_chat_id=staff_chat_id,
                )
            )
        else:
            tenant.staff_chat_id = staff_chat_id

        customer = await session.get(Customer, customer_id)
        if customer is None:
            # Fixture "fake client" chat = the staff group (clearly marked).
            session.add(
                Customer(
                    id=customer_id,
                    tenant_id=tenant_id,
                    telegram_user_id=7_700_000_999,
                    telegram_chat_id=staff_chat_id,
                    first_name="[клиент-фактура]",
                    first_seen_at=datetime.now(UTC),
                    last_seen_at=datetime.now(UTC),
                )
            )

        # Agent fixture (active) with the expectation to hand off on request.
        from src.db.models.agents import Agent

        agent = await session.get(Agent, agent_id)
        if agent is None:
            session.add(
                Agent(
                    id=agent_id,
                    tenant_id=tenant_id,
                    name="Live handoff agent",
                    system_prompt=AGENT_SYSTEM_PROMPT,
                    language="ru",
                    model=None,
                    temperature=0.3,
                    max_context_messages=12,
                    allowed_tools=[
                        "search_services",
                        "search_faq",
                        "save_lead",
                        "handoff_request",
                    ],
                    welcome_message="Здравствуйте! Чем могу помочь?",
                    is_active=True,
                )
            )
    # Seed step also needs the staff-relay Bot token setting — not stored in DB.
    return tenant_id, agent_id


async def _agent_messages(
    session: Any, tenant_id: uuid.UUID, conversation_id: uuid.UUID
) -> list[Any]:
    rows = (
        (
            await session.execute(
                select(Message).where(
                    Message.tenant_id == tenant_id,
                    Message.conversation_id == conversation_id,
                )
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def phase_a_agent_turn(
    tenant_id: uuid.UUID, tenant_agent_id: uuid.UUID
) -> tuple[str, uuid.UUID, dict[str, Any] | None, int | None]:
    """Phase A: one REAL agent turn asking for a live operator."""
    from src.db.models.agents import Agent

    settings = get_settings()
    provider = LLMProviderFactory.from_settings(settings)
    _print(
        f"PHASE A provider: {provider.provider_name} "
        f"(LLM_PROVIDER={settings.llm_provider}, url={settings.llm_api_base_url})"
    )
    loop = AgentLoop(provider, build_default_registry())

    factory = get_session_factory()
    async with factory() as session:
        agent = await session.get(Agent, tenant_agent_id)
        customer = await session.get(Customer, _fixed_uuid("customer:live-handoff"))
        assert agent is not None and customer is not None

        incoming = IncomingMessage(
            tenant_id=tenant_id,
            bot_id=_fixed_uuid("live-handoff-bot"),
            telegram_message_id=17_000_000 + int(datetime.now(UTC).timestamp()) % 10_000,
            user=UserIdentity(
                telegram_user_id=customer.telegram_user_id,
                telegram_chat_id=customer.telegram_chat_id,
                first_name="[клиент-фактура]",
            ),
            text="Хочу поговорить с оператором",
        )
        use_case = ProcessIncomingMessageUseCase(session, agent_loop=loop)
        result = await use_case.execute(incoming)

        handoff_row = (
            (
                await session.execute(
                    select(Handoff).where(Handoff.conversation_id == result.conversation_id)
                )
            )
            .scalars()
            .first()
        )
        staff_message_id = handoff_row.staff_message_id if handoff_row is not None else None
        agent_rows = await _agent_messages(session, tenant_id, result.conversation_id)
        meta = next((m.meta for m in agent_rows if m.meta), None)
        return result.response_text, result.conversation_id, meta, staff_message_id


async def _get_updates(token: str, offset: int) -> list[dict[str, Any]]:
    """Raw getUpdates through HTTP (manual check only — never in prod code)."""
    url = f"{TELEGRAM_API}/bot{token}/getUpdates"
    query: dict[str, Any] = {"timeout": 25, "offset": offset}
    query = {"timeout": 25, "offset": offset, "allowed_updates": '["message"]'}
    _ = query if "offset" in query else {}
    async with aiohttp.ClientSession() as http, http.post(url, json=query) as response:
        data = await response.json()
    if not data.get("ok"):
        return []
    return list(data.get("result", []))


async def phase_b_wait_staff_reply(token: str, staff_chat_id: int) -> tuple[str, str]:
    """Poll getUpdates; staff replies to notification → use case handles.

    '/back' → OK criterion; plain text → relayed to fixture client (partial).
    """
    deadline = datetime.now(UTC) + timedelta(seconds=_WAIT_SECONDS)
    offset = 0
    handled_update_ids: set[int] = set()

    while datetime.now(UTC) < deadline:
        await asyncio.sleep(2.0)
        try:
            updates = await _get_updates(token, offset)
        except aiohttp.ClientError as error:
            return "failed", f"getUpdates transport error: {type(error).__name__}"
        for update in updates:
            offset = max(offset, int(update.get("update_id", 0)) + 1)
            if update.get("update_id") in handled_update_ids:
                continue
            handled_update_ids.add(update.get("update_id", -1))
            message = update.get("message")
            if not isinstance(message, dict):
                continue
            chat = message.get("chat", {})
            if int(chat.get("id", 0)) != staff_chat_id:
                continue
            sender = message.get("from") or {}
            if sender.get("is_bot"):
                continue
            text = (message.get("text") or "").strip()
            if not text:
                continue

            factory = get_session_factory()
            async with factory() as session:
                incoming = IncomingMessage(
                    tenant_id=_fixed_uuid("tenant:live-handoff"),
                    bot_id=_fixed_uuid("live-handoff-bot"),
                    telegram_message_id=int(message["message_id"]),
                    user=UserIdentity(
                        telegram_user_id=int(sender.get("id", 0)),
                        telegram_chat_id=staff_chat_id,
                        first_name=str(sender.get("first_name") or "staff"),
                    ),
                    text=text,
                    is_staff_group=True,
                    raw_update={"message": message},
                )
                use_case = ProcessStaffReplyUseCase(session, bot=build_default_bot())
                outcome = await use_case.execute(incoming)

            if outcome.status == "handoff_returned":
                return "handoff_returned", text
            if outcome.status == "sent_to_client":
                return "sent_to_client", text
            print(f"PHASE B: staff message ignored ({outcome.reason})")  # noqa: T201
    return "failed", "timeout waiting for staff reply"


async def main() -> int:
    """Two-phase live check with honest endings."""
    _print("=" * 72)
    _print("LIVE HANDOFF CHECK — Stage 7.2 (A: tool → group; B: staff reply → /back)")
    _print("=" * 72)

    bot, staff_chat_id, token = _preflight()
    if bot is None or staff_chat_id is None or not token:
        return 2

    tenant_id, tenant_agent_id = await seed_fixture(staff_chat_id)
    _print("FIXTURE: tenant/agent/customer созданы (tenant_id скрыт, staff_chat скрыт)")

    response, _conversation_id, meta, staff_message_id = await phase_a_agent_turn(
        tenant_id, tenant_agent_id
    )
    _print("-" * 72)
    _print(f"PHASE A → ANSWER: {response[:300]}")
    if meta:
        _print(f"  TOOL_META: {meta}")
    if staff_message_id is None:
        _print("PHASE A FAILED: уведомление в группу не доставлено (см. TOOL_META).")
        return 3
    _print(f"  NOTIFICATION DELIVERED: staff_message_id={staff_message_id}")

    _print("-" * 72)
    _print(f"PHASE B: жду reply сотрудника в группе ({_WAIT_SECONDS} c)…")
    _print("  ОТВЕТЬТЕ reply-ом на уведомление; '/back' даёт HANDOFF LIVE OK.")

    status, detail = await phase_b_wait_staff_reply(token, staff_chat_id)

    if status == "handoff_returned":
        _print(f"PHASE B → staff message: {detail!r} → возврат в AI-режим подтверждён")
        _print("=" * 72)
        _print("HANDOFF LIVE OK")
        return 0
    if status == "sent_to_client":
        _print(f"PHASE B → reply доставлен клиенту-фактуре ({detail!r});")
        _print("('/back' не выполнен — честный PARTIAL, повторите '/back' и прогон целиком)")
        return 3
    _print(f"PHASE B FAILED: {detail}")
    return 3


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
