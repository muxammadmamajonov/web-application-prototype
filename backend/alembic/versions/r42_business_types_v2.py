"""Set the business-types taxonomy to the 12 canonical segments (Round 42)

Upserts the ``business_types`` platform setting so the new list applies in prod
regardless of any earlier admin override. The admin can still edit it later.

Revision ID: r42biztypesv2
Revises: r41clearcatalog
Create Date: 2026-05-31 16:00:00+00:00
"""
from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from alembic import op

revision = "r42biztypesv2"
down_revision = "r41clearcatalog"
branch_labels = None
depends_on = None

BUSINESS_TYPES = [
    {"slug": "electronics",  "name_uz": "Elektronika va telefonlar",          "name_ru": "Электроника и телефоны",       "emoji": "📱"},
    {"slug": "appliances",   "name_uz": "Maishiy texnika",                    "name_ru": "Бытовая техника",              "emoji": "🏠"},
    {"slug": "furniture",    "name_uz": "Mebel",                              "name_ru": "Мебель",                       "emoji": "🛋️"},
    {"slug": "auto",         "name_uz": "Avto va mototexnika",                "name_ru": "Авто и мототехника",           "emoji": "🚗"},
    {"slug": "clothing",     "name_uz": "Kiyim va oyoq kiyim",                "name_ru": "Одежда и обувь",               "emoji": "👗"},
    {"slug": "jewelry",      "name_uz": "Zargarlik buyumlari",                "name_ru": "Ювелирные изделия",            "emoji": "💍"},
    {"slug": "construction", "name_uz": "Qurilish va ta'mirlash mahsulotlari", "name_ru": "Стройматериалы и ремонт",     "emoji": "🏗️"},
    {"slug": "home_goods",   "name_uz": "Uy uchun mahsulotlar",               "name_ru": "Товары для дома",              "emoji": "🏡"},
    {"slug": "solar",        "name_uz": "Quyosh energiyasi uskunalari",       "name_ru": "Оборудование для солнечной энергии", "emoji": "☀️"},
    {"slug": "security",     "name_uz": "Xavfsizlik tizimlari",               "name_ru": "Системы безопасности",         "emoji": "📹"},
    {"slug": "kids",         "name_uz": "Bolalar mahsulotlari",               "name_ru": "Детские товары",               "emoji": "🧸"},
    {"slug": "other",        "name_uz": "Boshqa",                             "name_ru": "Другое",                       "emoji": "📦"},
]


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "INSERT INTO platform_settings (id, key, value) "
            "VALUES (:id, 'business_types', CAST(:val AS json)) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"id": uuid.uuid4(), "val": json.dumps(BUSINESS_TYPES)},
    )


def downgrade() -> None:
    pass
