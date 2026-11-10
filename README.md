# TGbots

Платформа для разработки индивидуальных Telegram-ботов с AI-агентами для российского бизнеса.

**Статус:** MVP-реализация в активной разработке (этапы 1–7 закрыты; этап 8 «Testing & Deployment» в работе). Модульный монолит: FastAPI + aiogram 3 + PostgreSQL 16 + облачный LLM через OpenAI-совместимый интерфейс (в MVP подключен локальный llama-server с моделью `qwen3.5-9b` через `127.0.0.1:1234/v1`).

## Что уже работает

- **Контур диалога (Agent Core):** state machine (`greeting → need_discovery → consultation → …`), контекст с окном истории, tool-петля (лимит 5 вызовов/ход), валидатор ответов, fallback'и.
- **Знания (этап 6):** каталог услуг + FAQ в PostgreSQL, полнотекстовый поиск на русском (`to_tsvector('russian'…)`), retrieval за интерфейсом `RetrievalService` (готов к pgvector); инструменты `search_services` / `search_faq` читают реальный каталог.
- **Лиды (этап 7.1):** инструмент `save_lead` с дедупликацией по диалогу, жизненным циклом и after-commit уведомлением в чат сотрудников.
- **Handoff (этап 7.2):** клиент/агент → `handoff_request` (строка в `handoffs` + флаг) → уведомление в группу сотрудников (реальная Telegram-доставка) → сотрудник отвечает **reply-ом** на уведомление → бот пересылает текст клиенту (роль `staff`) → `/back` возвращает диалог в AI-режим. Сбой LLM → системный handoff с `trigger=system_error`.
- **Mode multi-tenant:** tenant-изоляция на уровне приложения (TenantContext обязателен в каждом запросе) + частичные unique-ключи.
- **Безопасность:** секреты только в env, PII-маскирование в уведомлениях, `canary`-лог-фильтр `[REDACTED]`, отсутствие фолбэков на выдуманные данные.

## Технологический стек

| Слой | Технология |
|---|---|
| Runtime | Python 3.12+ |
| Telegram | aiogram 3.x |
| HTTP API | FastAPI (+ uvicorn) |
| Валидация | Pydantic 2, pydantic-settings |
| БД | PostgreSQL 16, SQLAlchemy 2 (async), Alembic |
| LLM | OpenAI-совместимый интерфейс (httpx); локально llama-server `qwen3.5-9b` |
| Тесты | pytest (маркеры unit/security/agent/database), pytest-asyncio, httpx mock-транспорт |
| Качество | ruff, mypy (strict), structlog |

Быстрая установка зависимостей: `pip install -e ".[dev]"` (ничего сверх pyproject не добавлялось).

## Quickstart

```powershell
# 1) окружение
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# 2) PostgreSQL 16 + миграции (локальная служба или docker compose up -d postgres)
#    DATABASE_URL по умолчанию: postgresql+asyncpg://tgbots:tgbots_dev@localhost:5432/tgbots
python -m alembic upgrade head

# 3) конфигурация .env (см. .env.example):
#    LLM_PROVIDER=openai, LLM_API_BASE_URL=http://127.0.0.1:1234/v1,
#    LLM_API_KEY=<локальный/реальный ключ>, LLM_DEFAULT_MODEL=qwen3.5-9b,
#    TELEGRAM_BOT_TOKEN=<токен>, TEST_STAFF_CHAT_ID=<служебная группа>
#    НИКОГДА не коммитите .env.

# 4) запуск приложения (HTTP API + webhook)
uvicorn src.main:app --host 0.0.0.0 --port 8000
#    проверки: GET /health → {"status":"ok","database":"connected",...}
```

## Тесты

Полный hardening-прогон одной командой (5 прогонов: security → database → agent → unit → full; логи в `test_reports\`):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_all_tests.ps1
```

Точечные категории:

```powershell
.venv\Scripts\python.exe -m pytest tests -m security -q
.venv\Scripts\python.exe -m pytest tests -m database -q
.venv\Scripts\python.exe -m pytest tests -m agent -q
.venv\Scripts\python.exe -m pytest tests -q          # полный регресс
```

Категории (Stage 8.1): `security` (6), `database` (4), `agent` (62), `unit` (265 — базовая категория; остальные добавляются поверх), `integration` — зарезервирован под живые БД-тесты (сейчас offline-набор их не содержит — честно), всего 265 тестов. CI: `.github/workflows/tests.yml` — ruff + mypy + pytest (offline; CI не трогает реальные Telegram/LLM端-точки).

## Live-проверки (ручные, не в CI)

Все скрипты запускаются из корня проекта с `PYTHONPATH=.` (важно для импорта `src/`):

```powershell
$env:PYTHONPATH = (Get-Location).Path

# Полный E2E на живом контуре (PostgreSQL + llama-server):
#   need LLM_PROVIDER=openai, LLM_API_BASE_URL=http://127.0.0.1:1234/v1,
#   LLM_API_KEY (для llama-server — фиктивный local-dev-key), LLM_DEFAULT_MODEL=qwen3.5-9b, LLM_TIMEOUT=120
$env:APP_ENV = "test"
.venv\Scripts\python.exe scripts\e2e_smoke.py            # EXIT=0, цены каталога в ответах

# Демо-каталог (идемпотентно: повторный запуск не дублирует):
.venv\Scripts\python.exe scripts\seed_demo_catalog.py

# Живая проверка handoff (Phase A: уведомление в группу == staff_message_id;
# Phase B: ответьте reply-ом на уведомление в группе, '/back' → HANDOFF LIVE OK):
.venv\Scripts\python.exe scripts\live_handoff_check.py   # окно ожидания: LIVE_HANDOFF_WAIT=сек

# Определение chat_id служебной группы (one-off):
.venv\Scripts\python.exe scripts\get_staff_chat_id.py
```

Примечания: `LLM_MAX_TOKENS=2048` рекомендуется для qwen3.5-9b (reasoning потребляет бюджет); Script `run_all_tests.ps1` лучше запускать с `-NoProfile` (в системе Admiral шум oh-my-posh не влияет на код возврата).

## Структура репозитория

```text
TGbots/
├── AGENTS.md                  # правила разработки
├── README.md                  # этот документ
├── pyproject.toml             # зависимости, ruff/mypy/pytest конфиг, маркеры тестов
├── .env.example               # шаблон env (без секретов)
├── .github/workflows/tests.yml# CI (ruff + mypy + offline pytest)
├── alembic.ini, migrations/   # схема БД (001 tables, 002 tenants.staff_chat_id)
├── docker-compose.yml         # Postgres 16 + app (для локального развёртывания)
├── docs/                      # PRODUCT_SPEC, ARCHITECTURE, DATABASE, SECURITY,
│                              # DEVELOPMENT_PLAN, API, AI_AGENT_SPEC
├── scripts/                   # e2e_smoke, live_handoff_check, seed_demo_catalog,
│                              # get_staff_chat_id, run_all_tests.ps1
├── src/
│   ├── api/                   # FastAPI: /health, Telegram webhook
│   ├── agent_core/            # state machine, policies, context, validator, agent loop
│   ├── application/           # use cases (process message, leads, token usage)
│   ├── config/                # pydantic-settings
│   ├── db/                    # engine, models, tenant-scoped repositories
│   ├── llm_providers/         # LLMProvider ABC, OpenAI-compatible, Mock
│   ├── knowledge/             # RetrievalService ABC + PostgreSQL FTS
│   ├── leads/                 # lead use case + after-commit queue
│   ├── handoff/               # staff reply use case
│   ├── notification/          # Notifier (Bot от вызывающего)
│   ├── telegram_adapter/      # aiogram routers, rate limiter, schemas
│   ├── tools/                 # Tool ABC, registry, real search/lead/handoff tools
│   └── monitoring/            # structured logging + secret masking
└── tests/                     # pytest (маркеры unit/security/agent/database)
```

## Известные ограничения (честно)

- **LLM:** прод-провайдер не выбран (SEC-OD-1); в MVP реально подключен локальный llama-server. CUDA-бэкенд llama-server локально не поднимался — модель работает на CPU-фоллбэке, ответы «холодной» модели занимают 15–200 с (belt LLM_TIMEOUT=120).
- **Telegram-транспорт E2E:** скрипты проверяют доставку уведомлений и ручной `/back`; реальные long-polling webhook-развёртывание и multi-tenant multi-bot токены — этап 8.2+.
- **RAG/pgvector:** не в MVP; интерфейс `RetrievalService` готов к замене (только `src/knowledge/search.py`).
- **Говорствуют «booking»:** таблица `bookings` существует, инструмент записи — этап 8.2+/по заказу (нужен проверяемый backend источник).
- **152-ФЗ** юридический аудит и итоговый DPA с LLM-провайдером — до коммерческого запуска (SEC-OD-9), вне кода.

## Документация

- [Продуктовая спецификация](docs/PRODUCT_SPEC.md) · [Архитектура](docs/ARCHITECTURE.md) · [База данных](docs/DATABASE.md) · [API](docs/API.md) · [AI-агент](docs/AI_AGENT_SPEC.md) · [Безопасность](docs/SECURITY.md) · [План разработки](docs/DEVELOPMENT_PLAN.md)
