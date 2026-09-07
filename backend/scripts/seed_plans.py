"""Insert default Lite / Pro / Scale plans (idempotent)."""
from __future__ import annotations

import asyncio
from decimal import Decimal

from sqlalchemy import select

from app.db import SessionLocal, engine
from app.models import Plan

# `Plan.code` is a plain string (R36 dropped the closed `PlanCode` enum — see
# app/models/enums.py) so the platform owner can mint arbitrary plan slugs.
# These four literals are this script's own baseline offering, not a closed
# set the schema enforces; a plan created via the platform HQ dashboard can
# use any other code.
PLANS = [
    {
        "code": "trial", "name_uz": "Demo",   "name_ru": "Демо",
        "description_uz": "14 kun bepul sinov — barcha imkoniyatlar.",
        "description_ru": "14 дней бесплатного теста — все возможности.",
        "price_monthly": Decimal("0"), "max_active_installments": 30, "sort_order": 0,
        "features": {"branches": 1, "staff": 2, "broadcasts_per_month": 5},
    },
    {
        "code": "lite",  "name_uz": "Lite",   "name_ru": "Lite",
        "description_uz": "Kichik do'konlar uchun · 50 ta faol bo'lib to'lash.",
        "description_ru": "Для небольших магазинов · 50 активных рассрочек.",
        "price_monthly": Decimal("240000"), "max_active_installments": 50, "sort_order": 10,
        "features": {"branches": 1, "staff": 2, "broadcasts_per_month": 10, "click": True, "payme": True},
    },
    {
        "code": "pro",   "name_uz": "Pro",    "name_ru": "Pro",
        "description_uz": "O'rta biznes uchun · 500 ta faol · brendlangan bot.",
        "description_ru": "Для среднего бизнеса · 500 активных · бот под брендом.",
        "price_monthly": Decimal("480000"), "max_active_installments": 500, "sort_order": 20,
        "features": {
            "branches": 5, "staff": 20, "broadcasts_per_month": 50,
            "click": True, "payme": True, "branded_bot": True,
            "ad_campaigns": True, "referrals": True,
        },
    },
    {
        "code": "custom", "name_uz": "Scale", "name_ru": "Scale",
        "description_uz": "Tarmoq do'konlar va banklar uchun. Bog'laning.",
        "description_ru": "Сетевые магазины и банки. Свяжитесь с нами.",
        "price_monthly": Decimal("0"), "max_active_installments": None, "sort_order": 30,
        "features": {"branches": None, "staff": None, "sso": True, "white_label": True, "sla": "dedicated"},
    },
]


async def main() -> None:
    async with SessionLocal() as db:
        for p in PLANS:
            existing = (await db.execute(select(Plan).where(Plan.code == p["code"]))).scalar_one_or_none()
            if existing:
                existing.name_uz = p["name_uz"]
                existing.name_ru = p["name_ru"]
                existing.description_uz = p["description_uz"]
                existing.description_ru = p["description_ru"]
                existing.price_monthly = p["price_monthly"]
                existing.max_active_installments = p["max_active_installments"]
                existing.features = p["features"]
                existing.sort_order = p["sort_order"]
                action = "updated"
            else:
                db.add(Plan(
                    code=p["code"],
                    name_uz=p["name_uz"], name_ru=p["name_ru"],
                    description_uz=p["description_uz"], description_ru=p["description_ru"],
                    price_monthly=p["price_monthly"],
                    max_active_installments=p["max_active_installments"],
                    features=p["features"], sort_order=p["sort_order"], is_active=True,
                ))
                action = "created"
            print(f"{action}: plan {p['code']}")
        await db.commit()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
