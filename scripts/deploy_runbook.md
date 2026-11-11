# Deploy Runbook — TGbots на российском VPS (Stage 8.3, polling-пилот)

Полная пошаговая инструкция развёртывания MVP на VPS **без домена** — бот
работает в polling-режиме (`getUpdates`). Решение принято пользователем:
аренда VPS и запуск шагов — на стороне оператора; этот runbook — карта.

Честные рамки: шаги §1–§5 проверены по командам, но **сам прогон на реальном
VPS в этой итерации не выполнялся** (Docker на dev-машине отсутствует) — см.
таблицу «локальная проверка vs VPS-verify» в конце файла.

---

## 1. Аренда VPS и первичная настройка

Референс (достаточно для MVP: приложение + PostgreSQL + LLM-проксирование):

| Параметр | Значение |
|---|---|
| Локация | РФ (152-ФЗ, `SECURITY.md` §16) |
| CPU / RAM / диск | 2 vCPU / 4 GB / 60 GB |
| ОС | Ubuntu 22.04 / 24.04 LTS |
| Доступ | SSH по ключу (парольный вход отключить) |

После создания сервера (первый вход как root или пользователь с sudo):

```bash
adduser deploy && usermod -aG sudo deploy          # рабочий пользователь
rsync ~/.ssh/authorized_keys /home/deploy/.ssh/    # ключ для deploy
apt update && apt full-upgrade -y
```

Файрвол (ufw): наружу — SSH, HTTP(80) и HTTPS(443) «на будущее»; всё
остальное закрыто:

```bash
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
ufw status
```

Примечание: порт 5432 (PostgreSQL) и 8000 (app) наружу НЕ открываются —
в compose порт БД привязан к `127.0.0.1`, а 8000 на будущее за прокси
(см. §7); при необходимости временно `ufw allow 8000/tcp`.

## 2. Установка Docker Engine (официальный apt-репозиторий)

```bash
# удалить возможные дистрибутивные пакеты
for p in docker.io docker-doc docker-compose podman-docker containerd runc; do apt-get remove -y $p; done

apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" > \
/etc/apt/sources.list.d/docker.list

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
usermod -aG docker deploy       # пере-логин после этого
docker run --rm hello-world     # контрольная проверка
```

## 3. Код и конфигурация

```bash
mkdir -p /opt/tgbots && cd /opt/tgbots
git clone <REPO_URL> .
```

Создать `/opt/tgbots/.env` (ВНЕ git, `chmod 600 .env`):

```ini
APP_ENV=prod
LOG_LEVEL=INFO

# ── PostgreSQL (заполните ОДИНАКОВЫЙ пароль в двух местах!) ──
POSTGRES_USER=tgbots
POSTGRES_PASSWORD=<сильный пароль>
POSTGRES_DB=tgbots
# host=postgres — имя сервиса compose (не localhost!):
DATABASE_URL=postgresql+asyncpg://tgbots:<сильный пароль>@postgres:5432/tgbots

# ── Telegram ──
TELEGRAM_BOT_TOKEN=<токен от BotFather>

# ── LLM (OpenAI-совместимый; для пилота — llama-server или облачный) ──
LLM_PROVIDER=openai
LLM_API_BASE_URL=http://<llm-host>:1234/v1
LLM_API_KEY=<ключ>
LLM_DEFAULT_MODEL=<модель>
LLM_TIMEOUT=120

# ── Защита webhook (NFR-1/2; в polling-режиме не активен, задать на будущее) ──
WEBHOOK_RATE_LIMIT_MAX=10
WEBHOOK_RATE_LIMIT_WINDOW=60
```

Пояснения к переменным:

| Переменная | Зачем |
|---|---|
| `POSTGRES_PASSWORD` | пароль БД-контейнера; **должен совпадать** с паролем в `DATABASE_URL` |
| `DATABASE_URL` | единственная строка подключения приложения/alembic; `+asyncpg` — драйвер SQLAlchemy |
| `TELEGRAM_BOT_TOKEN` | единственный потребитель update'ов и доставка уведомлений; никогда не логируется |
| `WEBHOOK_SECRET` | секрет заголовка webhook'а; в polling-режиме НЕ используется — заполнить при переходе (§7) |
| `LLM_*` | провайдер через OpenAI-совместимый интерфейс (SEC-OD-1) |

## 4. Подъём: postgres → миграции → app

```bash
cd /opt/tgbots

# 1) база
docker compose up -d postgres
docker compose ps                       # ждём healthy

# 2) миграции схемы (alembic ВНУТРИ контейнера)
docker compose run --rm app alembic upgrade head

# 3) приложение (webhook-режим по умолчанию; на VPS без домена можно не стартовать)
docker compose up -d app
```

Проверка здоровья:

```bash
curl -f http://localhost:8000/health
# ожидается: {"status":"ok","database":"connected", ...}
docker compose logs -f --tail 50 app     # json-логи с ротацией 10m×3
```

## 5. Polling-режим (пилот БЕЗ домена)

В compose заведён отдельный сервис `poll` (profile `polling`), который не
запускается вместе с `app` по умолчанию:

```bash
# старт polling-лупа как постоянного сервиса:
docker compose --profile polling up -d poll

# проверка: длинные getUpdates-циклы в логах (БЕЗ PII и без токена):
docker compose logs -f --tail 50 poll
# ожидается: polling.started / polling.update_handled ...
```

Механика честности (зафиксировано в коде `src/telegram_adapter/polling.py`):
`deleteWebhook` вызывается при старте (обязательно для `getUpdates`);
offset подтверждается строго после обработки; сбои обработки не стопорят
луп; сетевые ошибки → экспоненциальный backoff до 60 с. Идемпотентность
(`messages.telegram_message_id` unique) исключает дубль ответа при
повторной доставке.

Один токен = один потребитель: НЕ запускать `poll` и webhook-получение
одновременно; и НЕ запускать два экземпляра `poll` (Telegram отдаёт
update'ы только одному неподтверждённому клиенту).

## 6. Бэкапы: nightly pg_dump по cron + ежемесячный restore-тест (DB-OD-5)

Решение DB-OD-5 = **A-lite** (`docs/DATABASE.md` §14–15): ежедневный
pg_dump по cron + ежемесячный restore-тест; честное отклонение
RPO ≈ 24 ч (WAL/PITR — отдельная задача после MVP). Каталог дампов
`/opt/tgbots/backups` на зашифрованном диске VPS, наружу не открыт.

Создать каталог и строку cron **вручную** (команду ниже оператор ставит
само через `crontab -e`; runbook не настраивает планировщик):

```bash
mkdir -p /opt/tgbots/backups && chmod 700 /opt/tgbots/backups
```

```cron
# m  h  dom mon dow  command
0  3  *   *   *    cd /opt/tgbots && docker compose exec -T postgres pg_dump -U tgbots -Fc tgbots > backups/$(date +\%Y\%m\%d_\%H\%M\%S)_tgbots.dump && find /opt/tgbots/backups -name '*_tgbots.dump' -mtime +30 -delete
```

Внутри контейнера `pg_dump -U tgbots` работает по локальному сокету без
пароля (официальный образ postgres). Ротация 30 дней — строкой `find`
(это осознанный операторский автоделёт; локальный `.ps1`-инструмент
удаляет старое только по явному `-Prune`).

Ежемесячный restore-тест (вручную, раз в месяц — DATABASE.md §14):

```bash
# восстановить последний дамп во временную БД того же контейнера и сверить счётчики
cd /opt/tgbots
LATEST=$(ls -1 backups/*_tgbots.dump | sort | tail -1)
docker compose exec -T postgres psql -U tgbots -d postgres -c "DROP DATABASE IF EXISTS tgbots_restore_check;"
docker compose exec -T postgres psql -U tgbots -d postgres -c "CREATE DATABASE tgbots_restore_check;"
docker compose exec -T postgres pg_restore -U tgbots -d tgbots_restore_check --no-owner --no-privileges "$LATEST" 2>/dev/null
for t in tenants bots agents services customers conversations messages leads handoffs token_usage; do
  echo -n "$t: "
  docker compose exec -T postgres psql -U tgbots -d tgbots -t -A -c "SELECT count(*) FROM $t;"
  docker compose exec -T postgres psql -U tgbots -d tgbots_restore_check -t -A -c "SELECT count(*) FROM $t;"
done
docker compose exec -T postgres psql -U tgbots -d postgres -c "DROP DATABASE tgbots_restore_check;"
```

Расхождения `leads`/`messages` (живые записи ПОСЛЕ дампа) — ожидаемый дрейф;
остальные таблицы должны совпасть. Полная механика с честным комментарием —
локальный `scripts/db_restore_check.ps1` (не переписывается под VPS).

## 7. Будущее: домен + TLS → переход на webhook (ЗАГОТОВКА, не активная)

Когда появится домен (запись A → IP VPS) и TLS:

1. **Caddy** (авто-HTTPS) как сервис compose — заготовка, в compose НЕ включена:

   ```yaml
   # caddy:
   #   image: caddy:2-alpine
   #   ports: ["80:80", "443:443"]
   #   volumes:
   #     - ./Caddyfile:/etc/caddy/Caddyfile
   #     - caddy_data:/data
   # Caddyfile (минимум):
   #   bot.example.ru {
   #     reverse_proxy app:8000
   #   }
   ```

2. `WEBHOOK_SECRET=<секрет>` в `.env` (проверка заголовка
   `X-Telegram-Bot-Api-Secret-Token` заработает в endpoint'е).

3. `setWebhook` — ВРУЧНАЯ команда оператора (в коде запрещена):

   ```bash
   curl -s "https://api.telegram.org/bot<ТОКЕН>/setWebhook" \
        -d url="https://bot.example.ru/api/v1/telegram/webhook/<bot_id>" \
        -d secret_token="<WEBHOOK_SECRET>" \
        -d allowed_updates='["message"]'
   curl -s "https://api.telegram.org/bot<ТОКЕН>/getWebhookInfo"   # проверка
   ```

4. Остановить polling: `docker compose --profile polling stop poll`
   (одновременно getUpdates и webhook держать нельзя). Порт 8000 закрыть
   наружу (`ufw delete allow 8000/tcp`), наружу оставлять только 80/443.

5. Rate limiting webhook'а уже настраиваемый: `WEBHOOK_RATE_LIMIT_*` (NFR-1/2).

## 8. Эксплуатационная шпаргалка

| Действие | Команда |
|---|---|
| Логи app / poll | `docker compose logs -f app` / `docker compose logs -f poll` |
| Рестарт | `docker compose restart app` |
| Обновление кода | `git pull && docker compose build app poll && docker compose up -d app` (+ при смене схемы: `docker compose run --rm app alembic upgrade head`) |
| Проверка БД | `docker compose exec postgres psql -U tgbots -d tgbots -c 'SELECT count(*) FROM tenants;'` |
| Состояние сервисов | `docker compose ps` |
| Диск бэкапов | `ls -la /opt/tgbots/backups` (nightly, 30 дней ротации) |

---

### Локальная проверка vs VPS-verify

| Что | Статус |
|---|---|
| `docker-compose.yml` — YAML-валидность, сервисы/profile/healthcheck/volumes | проверено локально (python yaml.safe_load) |
| `Dockerfile` — структура, CMD webhook, curl для healthcheck | проверено статически |
| polling-контур (parser, routing, offset, backoff, shutdown) | проверено локально, 22 offline-теста на MockTransport |
| `python -m src.main poll` CLI-разбор аргументов | проверено локально (`--help`, выбор режима) |
| `docker build` (pull python:3.12-slim, pip install) | **VPS-verify** — Docker на dev-машине отсутствует |
| `docker compose up` / healthchecks / restart policy | **VPS-verify** |
| alembic upgrade head в контейнере | **VPS-verify** (локально миграции проверены на живой PostgreSQL 16) |
| pg_dump/pg_restore внутри контейнера + cron-строка | **VPS-verify** (команды эквивалентны локально проверенным ps1-инструментам) |
| polling живого Telegram (getUpdates, deleteWebhook) | **VPS-verify** (локальный код покрыт тестами; реальный Telegram не трогался из CI) |
