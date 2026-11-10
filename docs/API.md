# API.md — Спецификация HTTP API TGbots

> Статус: соответствует фактически реализованному коду (Stage 8.1). Единый MVP-сервер: FastAPI (`src/main.py`, `uvicorn src.main:app --factory`).

---

## 1. Общие положения

- Базовый префикс внутренних эндпоинтов: `/api/v1`.
- Формат: HTTP/JSON (смесь Telegram Update и служебных полей — см. ниже).
- Аутентификация клиентов внешним пользователям НЕ выдаётся: в MVP есть только Telegram webhook (защищённый секретом каждого бота) и `/health`.
- Ошибки не раскрывают stack trace, версии, DSN и секреты (SECURITY.md §10).
- CORS не открыт (нет браузерных клиентов в MVP).

## 2. GET /health

Health-check для мониторинга (реализовано в `src/api/health.py`).

**Ответ 200:**

```json
{
  "status": "ok",
  "environment": "dev",
  "version": "0.1.0",
  "database": "connected"
}
```

**Ответ 503** (БД недоступна):

```json
{
  "status": "degraded",
  "environment": "dev",
  "version": "0.1.0",
  "database": "disconnected"
}
```

Не требует аутентификации; не содержит персональных данных.

## 3. POST /api/v1/telegram/webhook/{bot_id}

Приём Telegram Update для конкретного бота (реализация: `src/api/webhook.py`).

### 3.1 Параметры и заголовки

| Часть | Значение |
|---|---|
| path | `bot_id: UUID` — внутренний идентификатор бота (таблица `bots`) |
| header | `X-Telegram-Bot-Api-Secret-Token: <secret>` — сверяется с `bots.webhook_secret_ciphertext` (или платформенным `WEBHOOK_SECRET` из env) |
| body | JSON — обычный Telegram Update (`{"update_id": …, "message": …}`) |

### 3.2 Коды ответов

| Код | Условие |
|---|---|
| 200 | Обработка выполнена (или обновление проигнорировано как non-message/дубликат) |
| 403 | Секретный заголовок отсутствует или неверен (`Invalid webhook secret`) |
| 404 | `bot_id` не существует или бот деактивирован (`Bot not found or inactive`) |
| 429 | Превышен пользовательский rate limit (`Too many requests. Please slow down.`) |

### 3.3 Тело ответа 200

```json
{
  "ok": true,
  "is_duplicate": false,
  "response_text": "Стрижка женская стоит от 1500 рублей. Это модельная стрижка с мытьём головы."
}
```

- `is_duplicate=true` — повторная доставка того же `telegram_message_id` (идемпотентность; ответ пуст, повторная обработка не выполняется).
- `response_text` — текст, предназначенный к отправке клиенту; в handoff-режиме (`conversations.handoff_active`) он **пуст**, потому что ответственность переходит сотруднику.
- Для групповых обновлений (staff) дополнительно возвращается поле `staff_reply` со статусом обработки (`sent_to_client` / `handoff_returned` / `ignored`).

### 3.4 Идемпотентность

Уникальный частичный индекс `uq_messages_tenant_tg_msg (tenant_id, telegram_message_id)` гарантирует: повторная доставка Telegram Update создаёт единственное сообщение и не дублирует ответ (NFR-8). Первый повтор возвращает `is_duplicate=true` без нового хода агента.

### 3.5 Rate limiting

`InMemoryRateLimiter` на пользователя: **10 запросов / 60 секунд** (значение in-memory, сбрасывается при перебалансировке процесса — документированное ограничение MVP). Превышение → 429.

### 3.6 Групповые обновления сотрудников (is_staff_group)

Правило маршрутизации (Stage 7.2, PLAN-OD-5): `update.message.chat.id < 0` (группа/супергруппа) → НЕ попадает в клиентский агент-путь; нормализуется с `is_staff_group=True` и обрабатывается `ProcessStaffReplyUseCase`:

- принимаются ТОЛЬКО reply-и на уведомление handoff (`reply_to_message.message_id == handoffs.staff_message_id`, статус `active`);
- текст reply: обычная строка → персистится (`messages.role='staff'`, natural id) и доставляется клиенту (`customers.telegram_chat_id`) **после** commit; `/back` (регистр не важен) → `handoffs.status='returned'`, `handoff_active=False`, state → `need_discovery` + подтверждение в группу;
- сообщения от ботов (`from.is_bot`), не-reply-сообщения и чужие чаты молча игнорируются (`staff_reply: "ignored"`).

## 4. Служебные и будущие endpoints

- Внутренний мониторинг/админ-API — Bearer `ADMIN_API_TOKEN` (подготовлено в `Settings`, реализация — этап 8.2+).
- Публичный API для клиентов — вне MVP (`PRODUCT_SPEC` FR-7.3).

## 5. Схемы кода

| Endpoint | Файл |
|---|---|
| `/health` | `src/api/health.py` |
| webhook (+ staff routing) | `src/api/webhook.py` |
| клиентская обработка | `src/application/process_incoming_message.py` |
| staff-обработка | `src/handoff/use_case.py` |
