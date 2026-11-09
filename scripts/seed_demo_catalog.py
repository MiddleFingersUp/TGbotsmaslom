"""Idempotent demo catalog seeder (fixture data only — no real client data).

Manual run against the live PostgreSQL (docs/DEVELOPMENT_PLAN.md Stage 6.1):
    .venv\\Scripts\\python.exe scripts\\seed_demo_catalog.py

Idempotency by natural keys:
- services: UNIQUE (tenant_id, name) — existing rows are UPDATED, missing
  rows INSERTed; re-running never duplicates;
- faqs: UNIQUE (tenant_id, question) — same update-or-insert rule.

All texts are fixture/demonstration content.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from decimal import Decimal
from typing import Any

std_out: Any = sys.stdout
std_out.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import func, select  # noqa: E402

_E2E_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "tgbots-demo-catalog")

DEMO_TENANT_NAME = "Демо-салон (fixture)"

DEMO_SERVICES: list[dict[str, Any]] = [
    {
        "name": "Стрижка женская",
        "description": "Модельная стрижка любой сложности с мытьём головы и укладкой.",
        "price_min": "1500.00",
        "price_max": None,
        "duration_minutes": 60,
        "tags": ["стрижка", "женская"],
    },
    {
        "name": "Стрижка мужская",
        "description": "Классическая и спортивная мужские стрижки, окантовка бороды.",
        "price_min": "800.00",
        "price_max": None,
        "duration_minutes": 45,
        "tags": ["стрижка", "мужская"],
    },
    {
        "name": "Окрашивание в один тон",
        "description": "Полное окрашивание базы профессиональными красителями.",
        "price_min": "4000.00",
        "price_max": "6000.00",
        "duration_minutes": 150,
        "tags": ["окрашивание", "женская"],
    },
    {
        "name": "Маникюр классический",
        "description": "Обрезной маникюр, шлифовка, покрытие гель-лаком.",
        "price_min": "2000.00",
        "price_max": None,
        "duration_minutes": 90,
        "tags": ["маникюр", "ноги"],
    },
    {
        "name": "Педикюр аппаратный",
        "description": "Аппаратная обработка стоп с покрытием или без.",
        "price_min": "2500.00",
        "price_max": None,
        "duration_minutes": 90,
        "tags": ["педикюр", "ноги"],
    },
    {
        "name": "Чистка лица комбинированная",
        "description": "Глубокое очищение пор, пилинг, маска по типу кожи.",
        "price_min": "3500.00",
        "price_max": "5000.00",
        "duration_minutes": 90,
        "tags": ["уход", "косметология"],
    },
    {
        "name": "Массаж спины",
        "description": "Классический лечебный массаж спины, 40 минут.",
        "price_min": "2200.00",
        "price_max": None,
        "duration_minutes": 40,
        "tags": ["массаж"],
    },
    {
        "name": "Ламинирование ресниц",
        "description": "Состав для укрепления и окрашивания натуральных ресниц.",
        "price_min": "2800.00",
        "price_max": None,
        "duration_minutes": 75,
        "tags": ["ресницы", "женская"],
    },
]

DEMO_FAQS: list[dict[str, Any]] = [
    {
        "question": "Вы работаете в воскресенье?",
        "answer": "Да, салон работает ежедневно с 9:00 до 21:00, включая воскресенье.",
        "tags": ["график"],
    },
    {
        "question": "Нужно ли записываться заранее?",
        "answer": "Рекомендуем записываться минимум за день: популярные мастера заняты быстро.",
        "tags": ["запись"],
    },
    {
        "question": "Есть ли парковка рядом с салоном?",
        "answer": "Да, бесплатная парковка во дворе здания для клиентов салона.",
        "tags": ["адрес"],
    },
    {
        "question": "Можно ли отменить или перенести запись?",
        "answer": "Да, не позднее чем за 3 часа до визита — просто напишите нам в чат.",
        "tags": ["запись"],
    },
    {
        "question": "Какие материалы используются для окрашивания?",
        "answer": "Только профессиональные сертифицированные красители безаммиачных линеек.",
        "tags": ["окрашивание", "материалы"],
    },
    {
        "question": "Делаете ли вы детские стрижки?",
        "answer": "Да, детские стрижки ведут два мастера по предварительной записи.",
        "tags": ["стрижка", "детская"],
    },
]


def _ns(tag: str) -> uuid.UUID:
    """Deterministic fixture id bound to the demo namespace."""
    return uuid.uuid5(_E2E_NAMESPACE, tag)


async def seed() -> uuid.UUID:
    """Upsert demo catalog rows; return the demo tenant id binding."""
    from src.config.settings import get_settings
    from src.db.models.faqs import FAQ
    from src.db.models.services import Service
    from src.db.models.tenants import Tenant
    from src.db.session import get_session_factory

    _ = get_settings()  # refresh env snapshot
    tenant_id = _ns("tenant:demo-salon")
    created = updated = 0

    factory = get_session_factory()
    async with factory() as session, session.begin():
        tenant = await session.get(Tenant, tenant_id)
        if tenant is None:
            session.add(
                Tenant(
                    id=tenant_id,
                    name=DEMO_TENANT_NAME,
                    notes="Fixture-данные только для разработки и демо; не для прод.",
                )
            )
            await session.flush()

        for svc in DEMO_SERVICES:
            existing_path = await session.execute(
                select(Service).where(Service.tenant_id == tenant_id, Service.name == svc["name"])
            )
            service = existing_path.scalars().first()
            if service is None:
                session.add(
                    Service(
                        id=_ns("service:" + svc["name"]),
                        tenant_id=tenant_id,
                        name=svc["name"],
                        description=svc["description"],
                        price_min=Decimal(svc["price_min"]),
                        price_max=(
                            Decimal(svc["price_max"]) if svc["price_max"] is not None else None
                        ),
                        duration_minutes=svc["duration_minutes"],
                        tags=list(svc["tags"]),
                        currency="RUB",
                    )
                )
                created += 1
            else:
                service.description = svc["description"]
                service.price_min = Decimal(svc["price_min"])
                service.price_max = (
                    Decimal(svc["price_max"]) if svc["price_max"] is not None else None
                )
                service.duration_minutes = svc["duration_minutes"]
                service.tags = list(svc["tags"])
                updated += 1

        for faq in DEMO_FAQS:
            existing = await session.execute(
                select(FAQ).where(FAQ.tenant_id == tenant_id, FAQ.question == faq["question"])
            )
            row = existing.scalars().first()
            if row is None:
                session.add(
                    FAQ(
                        id=_ns("faq:" + faq["question"]),
                        tenant_id=tenant_id,
                        question=faq["question"],
                        answer=faq["answer"],
                        tags=list(faq["tags"]),
                    )
                )
                created += 1
            else:
                row.answer = faq["answer"]
                row.tags = list(faq["tags"])
                updated += 1

    tenant_key = tenant_id

    # Honest totals: count AFTER commit in a fresh session (autoflush=False
    # would otherwise hide pending rows inside the seeding transaction).
    from sqlalchemy import select as sa_select
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(get_settings().database_url, future=True)
    async with engine.connect() as conn:
        total_services = await conn.scalar(
            sa_select(func.count()).select_from(Service).where(Service.tenant_id == tenant_key)
        )
        total_faqs = await conn.scalar(
            sa_select(func.count()).select_from(FAQ).where(FAQ.tenant_id == tenant_key)
        )
    await engine.dispose()

    print(
        f"SEED OK: tenant general={DEMO_TENANT_NAME!r} "
        f"created={created} updated={updated} "
        f"services_total={total_services} faqs_total={total_faqs}"
    )
    return tenant_key


async def main() -> int:
    """Run the seeder (safe to repeat: totals must stay equal on re-runs)."""
    await seed()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
