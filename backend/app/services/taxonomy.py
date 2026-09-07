"""Editable onboarding taxonomies — business types + acquisition channels.

Stored in the ``PlatformSetting`` key/value table under ``business_types`` and
``how_found_options`` so HQ can edit them in the dashboard. When a key is
absent we fall back to these curated defaults (and seed nothing — the GET just
returns the default until an admin saves an override).

``general`` and ``other`` are reserved client-side options (show-all / free
text) and are intentionally NOT part of the editable list.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PlatformSetting

BUSINESS_TYPES_KEY = "business_types"
HOW_FOUND_KEY = "how_found_options"

DEFAULT_BUSINESS_TYPES: list[dict[str, str]] = [
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

DEFAULT_HOW_FOUND: list[dict[str, str]] = [
    {"value": "google",    "label_uz": "Google qidiruv",   "label_ru": "Поиск Google"},
    {"value": "chatgpt",   "label_uz": "ChatGPT / AI",     "label_ru": "ChatGPT / AI"},
    {"value": "instagram", "label_uz": "Instagram",        "label_ru": "Instagram"},
    {"value": "telegram",  "label_uz": "Telegram",         "label_ru": "Telegram"},
    {"value": "tiktok",    "label_uz": "TikTok",           "label_ru": "TikTok"},
    {"value": "youtube",   "label_uz": "YouTube",          "label_ru": "YouTube"},
    {"value": "friend",    "label_uz": "Do'st / tavsiya",  "label_ru": "Друг / рекомендация"},
]


async def _get_setting_list(db: AsyncSession, key: str, default: list[dict[str, Any]]) -> list[dict[str, Any]]:
    row = (await db.execute(
        select(PlatformSetting).where(PlatformSetting.key == key)
    )).scalar_one_or_none()
    val = row.value if row else None
    # The PlatformSetting value column is JSON; an admin override may store the
    # list directly or wrapped as {"items": [...]}. Accept both, else default.
    if isinstance(val, list) and val:
        return val
    if isinstance(val, dict) and isinstance(val.get("items"), list) and val["items"]:
        return val["items"]
    return default


async def get_business_types(db: AsyncSession) -> list[dict[str, Any]]:
    return await _get_setting_list(db, BUSINESS_TYPES_KEY, DEFAULT_BUSINESS_TYPES)


async def get_how_found_options(db: AsyncSession) -> list[dict[str, Any]]:
    return await _get_setting_list(db, HOW_FOUND_KEY, DEFAULT_HOW_FOUND)
