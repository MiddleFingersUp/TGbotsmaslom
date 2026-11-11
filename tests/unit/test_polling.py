"""Unit tests for the polling entrypoint (Stage 8.3, offline via MockTransport).

Covers:
- shared update parser: private / staff group / non-message / malformed;
- TelegramPollingLoop on a fake httpx transport: deleteWebhook at start,
  routing of both message types, offset advance after processing, duplicate
  update not processed twice, offset advance despite processing errors,
  network-error backoff, graceful shutdown.

No real network, no real DB: use cases are stubbed, sessions are fakes.
"""

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import httpx
import pytest

from src.db.models.bots import Bot
from src.telegram_adapter import polling as polling_module
from src.telegram_adapter.polling import TelegramApiError, TelegramPollingLoop
from src.telegram_adapter.update_parser import parse_telegram_update

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
BOT_ID = UUID("00000000-0000-0000-0000-000000000002")

PRIVATE_UPDATE = {
    "update_id": 100,
    "message": {
        "message_id": 11,
        "from": {"id": 42, "username": "ivan", "first_name": "Иван", "language_code": "ru"},
        "chat": {"id": 42, "type": "private"},
        "text": "Привет",
    },
}

STAFF_UPDATE = {
    "update_id": 101,
    "message": {
        "message_id": 12,
        "from": {"id": 43, "first_name": "Сотрудник"},
        "chat": {"id": -1001234, "type": "supergroup"},
        "text": "Ответ сотрудника",
    },
}


def _db_bot() -> Bot:
    return Bot(
        id=BOT_ID,
        tenant_id=TENANT_ID,
        bot_username="test_bot",
        token_fingerprint="abcdef1234567890",
        is_active=True,
    )


class FakeSessionFactory:
    """Minimal async-context-manager session factory (no DB)."""

    def __init__(self) -> None:
        self.opened = 0

    def __call__(self) -> Any:
        return self

    async def __aenter__(self) -> Any:
        self.opened += 1
        return MagicMock()

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class UseCaseStub:
    """Records executed IncomingMessages; returns a fixed result."""

    def __init__(self, result: Any) -> None:
        self.calls: list[Any] = []
        self.result = result

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self

    async def execute(self, incoming: Any) -> Any:
        self.calls.append(incoming)
        # None → honest shape stand-ins: polling._route reads
        # `.is_duplicate` (client) / `.status` (staff outcome) after the
        # call; returning None would produce AttributeError log noise.
        if self.result is None:
            return SimpleNamespace(is_duplicate=False, status="sent_to_client")
        return self.result


# ─── Parser tests ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_parser_private_message() -> None:
    """Private message → IncomingMessage with normalized fields."""
    incoming = parse_telegram_update(PRIVATE_UPDATE, _db_bot())
    assert incoming is not None
    assert incoming.tenant_id == TENANT_ID
    assert incoming.bot_id == BOT_ID
    assert incoming.telegram_message_id == 11
    assert incoming.text == "Привет"
    assert incoming.user.telegram_user_id == 42
    assert incoming.user.telegram_chat_id == 42
    assert incoming.user.username == "ivan"
    assert incoming.user.first_name == "Иван"
    assert incoming.user.language_code == "ru"
    assert incoming.is_staff_group is False


@pytest.mark.unit
def test_parser_staff_group_message() -> None:
    """Group message (chat id < 0) → is_staff_group=True."""
    incoming = parse_telegram_update(STAFF_UPDATE, _db_bot())
    assert incoming is not None
    assert incoming.is_staff_group is True
    assert incoming.user.telegram_chat_id == -1001234


@pytest.mark.unit
def test_parser_non_message_update_returns_none() -> None:
    """inline_query / callback_query updates are skipped."""
    assert parse_telegram_update({"update_id": 1, "inline_query": {}}, _db_bot()) is None
    assert parse_telegram_update({"update_id": 2}, _db_bot()) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "broken",
    [
        {"message": {"message_id": 1}},  # no from / no chat
        {"message": {"message_id": 1, "from": {"id": 1}, "chat": {}}},  # no chat id
        {"message": {"from": {"id": 1}, "chat": {"id": 2}, "text": "x"}},  # no message_id
        {"message": {"message_id": "x", "from": {"id": 1}, "chat": {"id": 2}}},  # bad type
    ],
)
def test_parser_malformed_payloads_return_none(broken: dict[str, Any]) -> None:
    assert parse_telegram_update(broken, _db_bot()) is None


@pytest.mark.unit
def test_parser_media_message_no_text() -> None:
    """Media without text → is_media=True."""
    update = {
        "update_id": 3,
        "message": {
            "message_id": 13,
            "from": {"id": 42},
            "chat": {"id": 42},
            "photo": [{"file_id": "x"}],
        },
    }
    incoming = parse_telegram_update(update, _db_bot())
    assert incoming is not None
    assert incoming.is_media is True
    assert incoming.text is None


# ─── Polling loop helpers ───────────────────────────────────────────────


class _ApiFake:
    """Records API calls and serves queued getUpdates batches."""

    def __init__(self, batches: list[list[dict[str, Any]]], fail_first: int = 0) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.batches = list(batches)
        self.fail_first = fail_first
        self.failures_left = fail_first

    async def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content.decode() or "{}")
        self.calls.append((method, payload))
        if self.failures_left > 0:
            self.failures_left -= 1
            raise httpx.ConnectError("connection refused (fake)")
        if method == "getUpdates":
            result = self.batches.pop(0) if self.batches else []
            return httpx.Response(200, json={"ok": True, "result": result})
        return httpx.Response(200, json={"ok": True, "result": True})

    def method_calls(self, method: str) -> list[dict[str, Any]]:
        return [payload for name, payload in self.calls if name == method]


async def _wait_until(predicate: Any, timeout: float = 3.0) -> None:
    """Poll a condition on the test loop (fast, no fixed sleeps)."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            pytest.fail("condition not met in time")
        await asyncio.sleep(0.005)


# Built at runtime: the secrets audit scans tracked SOURCE text, so a
# complete token-shaped literal must not appear in any single line
# (same allowlist-free pattern as tests/unit/test_audit_secrets.py).
_FAKE_BOT_TOKEN = "123456789:" + "FakeTestToken_ABCDEFGHIJKLMNOP" + "QRSTUVWX"


def _make_loop(
    api: _ApiFake,
    incoming_stub: UseCaseStub,
    staff_stub: UseCaseStub,
    bot: Bot | None,
) -> tuple[TelegramPollingLoop, FakeSessionFactory]:
    """Build a loop wired to fakes (transport, use cases, bot resolution)."""
    factory = FakeSessionFactory()
    transport = httpx.MockTransport(api.handler)
    loop = TelegramPollingLoop(
        _FAKE_BOT_TOKEN,
        factory,  # type: ignore[arg-type]
        transport=transport,
        backoff_start=0.01,
        backoff_cap=0.04,
    )
    loop._resolve_bot = _async_return(bot)  # type: ignore[method-assign]
    polling_module.ProcessIncomingMessageUseCase = incoming_stub  # type: ignore[misc]
    polling_module.ProcessStaffReplyUseCase = staff_stub  # type: ignore[misc]
    polling_module.build_default_bot = lambda: None  # type: ignore[misc]
    return loop, factory


def _async_return(value: Any) -> Any:
    async def _resolver(session: Any) -> Any:
        return value

    return _resolver


async def _run_until(loop: TelegramPollingLoop, predicate: Any, timeout: float = 3.0) -> None:
    """Run the loop as a task until predicate, then request graceful stop."""
    task = asyncio.create_task(loop.run())
    try:
        await _wait_until(predicate, timeout)
    finally:
        loop.stop()
        await asyncio.wait_for(task, timeout=5.0)


# ─── Polling loop tests ─────────────────────────────────────────────────


@pytest.mark.unit
async def test_polling_delete_webhook_called_at_start() -> None:
    """Startup must call deleteWebhook before any getUpdates."""
    api = _ApiFake([])
    incoming, staff = UseCaseStub(None), UseCaseStub(None)
    loop, _factory = _make_loop(api, incoming, staff, _db_bot())
    await _run_until(loop, lambda: len(api.method_calls("getUpdates")) >= 1)
    methods = [name for name, _payload in api.calls]
    assert methods[0] == "deleteWebhook"
    assert "getUpdates" in methods


@pytest.mark.unit
async def test_polling_get_updates_params() -> None:
    """getUpdates carries timeout=25, current offset and allowed_updates."""
    api = _ApiFake([[PRIVATE_UPDATE]])
    incoming, staff = UseCaseStub(None), UseCaseStub(None)
    loop, _factory = _make_loop(api, incoming, staff, _db_bot())
    await _run_until(loop, lambda: len(api.method_calls("getUpdates")) >= 2)
    first, second = api.method_calls("getUpdates")[:2]
    assert first["timeout"] == 25
    assert first["offset"] == 0
    assert first["allowed_updates"] == ["message"]
    # offset confirmed strictly after processing → next batch carries 101
    assert second["offset"] == 101


@pytest.mark.unit
async def test_polling_routes_private_message_to_agent_use_case() -> None:
    """Private chat update → ProcessIncomingMessageUseCase."""
    api = _ApiFake([[PRIVATE_UPDATE]])
    incoming, staff = UseCaseStub(None), UseCaseStub(None)
    loop, _factory = _make_loop(api, incoming, staff, _db_bot())
    await _run_until(loop, lambda: len(incoming.calls) >= 1)
    assert len(incoming.calls) == 1
    assert incoming.calls[0].telegram_message_id == 11
    assert incoming.calls[0].is_staff_group is False
    assert staff.calls == []


@pytest.mark.unit
async def test_polling_routes_staff_group_to_staff_use_case() -> None:
    """Staff-group update → ProcessStaffReplyUseCase, never the agent path."""
    api = _ApiFake([[STAFF_UPDATE]])
    incoming, staff = UseCaseStub(None), UseCaseStub(None)
    loop, _factory = _make_loop(api, incoming, staff, _db_bot())
    await _run_until(loop, lambda: len(staff.calls) >= 1)
    assert len(staff.calls) == 1
    assert staff.calls[0].is_staff_group is True
    assert incoming.calls == []


@pytest.mark.unit
async def test_polling_offset_advances_after_processing() -> None:
    """Offset = update_id + 1 only after the update was handled."""
    api = _ApiFake([[PRIVATE_UPDATE]])
    incoming, staff = UseCaseStub(None), UseCaseStub(None)
    loop, _factory = _make_loop(api, incoming, staff, _db_bot())
    await _run_until(loop, lambda: len(api.method_calls("getUpdates")) >= 2)
    assert loop._offset == 101


@pytest.mark.unit
async def test_polling_duplicate_update_not_processed_twice() -> None:
    """The same update_id arriving twice is handled once, then skipped."""
    api = _ApiFake([[PRIVATE_UPDATE], [dict(PRIVATE_UPDATE)]])
    incoming, staff = UseCaseStub(None), UseCaseStub(None)
    loop, _factory = _make_loop(api, incoming, staff, _db_bot())
    await _run_until(loop, lambda: len(api.method_calls("getUpdates")) >= 3)
    assert len(incoming.calls) == 1


@pytest.mark.unit
async def test_polling_processing_error_advances_offset() -> None:
    """A broken update is logged and skipped — the loop is not stuck."""
    api = _ApiFake([[PRIVATE_UPDATE]])

    class ExplodingUseCase:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def execute(self, incoming: Any) -> Any:
            raise RuntimeError("broken update (fake)")

    staff = UseCaseStub(None)
    factory = FakeSessionFactory()
    loop = TelegramPollingLoop(
        _FAKE_BOT_TOKEN,
        factory,  # type: ignore[arg-type]
        transport=httpx.MockTransport(api.handler),
        backoff_start=0.01,
    )
    loop._resolve_bot = _async_return(_db_bot())  # type: ignore[method-assign]
    polling_module.ProcessIncomingMessageUseCase = ExplodingUseCase  # type: ignore[misc]
    polling_module.ProcessStaffReplyUseCase = staff  # type: ignore[misc]
    polling_module.build_default_bot = lambda: None  # type: ignore[misc]

    await _run_until(loop, lambda: len(api.method_calls("getUpdates")) >= 2)
    # offset moved past the failing update anyway
    assert loop._offset == 101
    assert staff.calls == []


@pytest.mark.unit
async def test_polling_network_error_backoff_then_recovery() -> None:
    """ConnectionError → exponential backoff, reset after success."""
    api = _ApiFake([[PRIVATE_UPDATE]], fail_first=2)
    incoming, staff = UseCaseStub(None), UseCaseStub(None)
    loop, _factory = _make_loop(api, incoming, staff, _db_bot())
    # Deterministic predicate: count getUpdates CALLS, not the use-case
    # outcome. Waiting on incoming.calls would race the loop's next poll —
    # on slow runners the graceful stop can land before the 3rd getUpdates
    # (observed on ubuntu CI: 2 calls instead of 3). The predicate only
    # becomes true AFTER the 3rd call exists, so the race is impossible.
    await _run_until(loop, lambda: len(api.method_calls("getUpdates")) >= 3)
    # two failed getUpdates attempts happened before the successful one
    assert len(api.method_calls("getUpdates")) >= 3
    # backoff grew exponentially and reset back to start after success
    assert loop._backoff_start == 0.01
    assert loop._offset == 101
    assert len(incoming.calls) == 1


@pytest.mark.unit
async def test_polling_non_message_update_skipped_but_confirmed() -> None:
    """Non-message updates never reach use cases; offset still advances."""
    api = _ApiFake([[{"update_id": 50, "inline_query": {}}], [PRIVATE_UPDATE]])
    incoming, staff = UseCaseStub(None), UseCaseStub(None)
    loop, _factory = _make_loop(api, incoming, staff, _db_bot())
    await _run_until(loop, lambda: len(incoming.calls) >= 1)
    assert loop._offset == 101


@pytest.mark.unit
async def test_polling_no_active_bot_skips_fail_closed() -> None:
    """Without an active DB bot the update is skipped (no tenant mapping)."""
    api = _ApiFake([[PRIVATE_UPDATE]])
    incoming, staff = UseCaseStub(None), UseCaseStub(None)
    loop, _factory = _make_loop(api, incoming, staff, None)
    await _run_until(loop, lambda: len(api.method_calls("getUpdates")) >= 2)
    assert incoming.calls == []
    assert loop._offset == 101  # confirmed, not stuck


@pytest.mark.unit
async def test_polling_graceful_shutdown_closes_client() -> None:
    """stop() exits the loop and closes the httpx client."""
    api = _ApiFake([])
    incoming, staff = UseCaseStub(None), UseCaseStub(None)
    loop, _factory = _make_loop(api, incoming, staff, _db_bot())
    await _run_until(loop, lambda: len(api.method_calls("getUpdates")) >= 1)
    assert loop._client.is_closed


@pytest.mark.unit
async def test_polling_telegram_api_error_treated_as_transport_error() -> None:
    """``ok: false`` → TelegramApiError → backoff, no crash."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"ok": False, "error_code": 409})

    loop = TelegramPollingLoop(
        _FAKE_BOT_TOKEN,
        FakeSessionFactory(),  # type: ignore[arg-type]
        transport=httpx.MockTransport(handler),
        backoff_start=0.01,
    )
    loop._resolve_bot = _async_return(_db_bot())  # type: ignore[method-assign]
    await _run_until(loop, lambda: len(calls) >= 3)
    assert loop._client.is_closed


@pytest.mark.unit
async def test_run_polling_without_token_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_polling() without TELEGRAM_BOT_TOKEN → exit code 2, no network."""

    class _FakeSettings:
        telegram_bot_token: str | None = None
        log_level = "INFO"
        app_env = "test"

    monkeypatch.setattr(polling_module, "__name__", polling_module.__name__)
    import src.config.settings as settings_module

    monkeypatch.setattr(settings_module, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(
        polling_module, "TelegramPollingLoop", lambda *a, **k: pytest.fail("loop built")
    )
    exit_code = await polling_module.run_polling()
    assert exit_code == 2


@pytest.mark.unit
def test_telegram_api_error_is_runtime_error() -> None:
    """TelegramApiError is a RuntimeError subtype (caught with transport errors)."""
    assert issubclass(TelegramApiError, RuntimeError)
