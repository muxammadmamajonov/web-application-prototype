"""Seed catalog categories per business type (Round 43)

Inserts the category list for each of the 12 business segments (uz + ru names,
an emoji, tagged to its business type). Field/identifier schemas are left empty
— they're defined per category afterwards. Idempotent: ON CONFLICT (slug) DO
NOTHING, so re-runs and manual edits are safe.

Revision ID: r43seedcategories
Revises: r42biztypesv2
Create Date: 2026-05-31 17:00:00+00:00
"""
from __future__ import annotations

import json
import re
import uuid

import sqlalchemy as sa
from alembic import op

revision = "r43seedcategories"
down_revision = "r42biztypesv2"
branch_labels = None
depends_on = None

# (business_type slug, name_uz, name_ru, emoji)
CATEGORIES: list[tuple[str, str, str, str]] = [
    # 📱 Elektronika va telefonlar
    ("electronics", "Smartfonlar", "Смартфоны", "📱"),
    ("electronics", "Tugmali telefonlar", "Кнопочные телефоны", "📞"),
    ("electronics", "Planshetlar", "Планшеты", "📲"),
    ("electronics", "Noutbuklar", "Ноутбуки", "💻"),
    ("electronics", "Kompyuterlar", "Компьютеры", "🖥️"),
    ("electronics", "Monitorlar", "Мониторы", "🖥️"),
    ("electronics", "Printerlar", "Принтеры", "🖨️"),
    ("electronics", "Smart soatlar", "Смарт-часы", "⌚"),
    ("electronics", "Quloqchinlar", "Наушники", "🎧"),
    ("electronics", "Kolonkalar", "Колонки", "🔊"),
    ("electronics", "Powerbanklar", "Повербанки", "🔋"),
    ("electronics", "Telefon aksessuarlari", "Аксессуары для телефонов", "🔌"),
    ("electronics", "Kompyuter aksessuarlari", "Аксессуары для компьютеров", "⌨️"),
    ("electronics", "O'yin konsollari", "Игровые консоли", "🎮"),
    ("electronics", "Router va modemlar", "Роутеры и модемы", "📶"),
    # 🏠 Maishiy texnika
    ("appliances", "Muzlatgichlar", "Холодильники", "🧊"),
    ("appliances", "Kir yuvish mashinalari", "Стиральные машины", "🧺"),
    ("appliances", "Gaz plitalari", "Газовые плиты", "🔥"),
    ("appliances", "Elektr plitalar", "Электроплиты", "🍳"),
    ("appliances", "Duxovkalar", "Духовки", "🍞"),
    ("appliances", "Mikroto'lqinli pechlar", "Микроволновые печи", "♨️"),
    ("appliances", "Changyutgichlar", "Пылесосы", "🧹"),
    ("appliances", "Idish yuvish mashinalari", "Посудомоечные машины", "🍽️"),
    ("appliances", "Konditsionerlar", "Кондиционеры", "❄️"),
    ("appliances", "Isitgichlar", "Обогреватели", "🌡️"),
    ("appliances", "Ventilyatorlar", "Вентиляторы", "💨"),
    ("appliances", "Havo tozalagichlar", "Очистители воздуха", "🌬️"),
    ("appliances", "Televizorlar", "Телевизоры", "📺"),
    ("appliances", "Suv isitkichlar", "Водонагреватели", "💧"),
    ("appliances", "Oshxona texnikasi", "Кухонная техника", "🍴"),
    # 🛋️ Mebel
    ("furniture", "Divanlar", "Диваны", "🛋️"),
    ("furniture", "Krovatlar", "Кровати", "🛏️"),
    ("furniture", "Shkaflar", "Шкафы", "🗄️"),
    ("furniture", "Komodlar", "Комоды", "🗃️"),
    ("furniture", "Stol va stullar", "Столы и стулья", "🪑"),
    ("furniture", "Oshxona mebellari", "Кухонная мебель", "🍽️"),
    ("furniture", "Yumshoq mebellar", "Мягкая мебель", "🛋️"),
    ("furniture", "Ofis mebellari", "Офисная мебель", "💼"),
    ("furniture", "Bolalar mebellari", "Детская мебель", "🧸"),
    ("furniture", "Matraslar", "Матрасы", "🛏️"),
    ("furniture", "Mebel to'plamlari", "Мебельные комплекты", "🛋️"),
    # 🚗 Avto va mototexnika
    ("auto", "Avtomobillar", "Автомобили", "🚗"),
    ("auto", "Mototsikllar", "Мотоциклы", "🏍️"),
    ("auto", "Skuterlar", "Скутеры", "🛵"),
    ("auto", "Elektro skuterlar", "Электросамокаты", "🛴"),
    ("auto", "Velosipedlar", "Велосипеды", "🚲"),
    ("auto", "Diskalar", "Диски", "⚙️"),
    ("auto", "Shinalar", "Шины", "🛞"),
    ("auto", "Akkumulyatorlar", "Аккумуляторы", "🔋"),
    ("auto", "Avto audio", "Автоаудио", "🔊"),
    ("auto", "Avto aksessuarlar", "Автоаксессуары", "🧰"),
    ("auto", "Ehtiyot qismlar", "Запчасти", "🔧"),
    ("auto", "Moy va texnik suyuqliklar", "Масла и технические жидкости", "🛢️"),
    # 👗 Kiyim va oyoq kiyim
    ("clothing", "Erkaklar kiyimi", "Мужская одежда", "👔"),
    ("clothing", "Ayollar kiyimi", "Женская одежда", "👗"),
    ("clothing", "Bolalar kiyimi", "Детская одежда", "👕"),
    ("clothing", "Erkaklar oyoq kiyimi", "Мужская обувь", "👞"),
    ("clothing", "Ayollar oyoq kiyimi", "Женская обувь", "👠"),
    ("clothing", "Sport kiyimlari", "Спортивная одежда", "🏃"),
    ("clothing", "Sumkalar", "Сумки", "👜"),
    ("clothing", "Soatlar", "Часы", "⌚"),
    ("clothing", "Aksessuarlar", "Аксессуары", "🧢"),
    # 💍 Zargarlik buyumlari
    ("jewelry", "Uzuklar", "Кольца", "💍"),
    ("jewelry", "Ziraklar", "Серьги", "💎"),
    ("jewelry", "Brasletlar", "Браслеты", "📿"),
    ("jewelry", "Zanjirlar", "Цепочки", "🔗"),
    ("jewelry", "Kulonlar", "Кулоны", "💎"),
    ("jewelry", "Komplektlar", "Комплекты", "💍"),
    ("jewelry", "Erkaklar zargarligi", "Мужские украшения", "💍"),
    ("jewelry", "Kumush buyumlar", "Серебряные изделия", "🥈"),
    ("jewelry", "Tilla buyumlar", "Золотые изделия", "🥇"),
    # 🏗️ Qurilish va ta'mirlash mahsulotlari
    ("construction", "Sement", "Цемент", "🪨"),
    ("construction", "G'isht", "Кирпич", "🧱"),
    ("construction", "Armatura", "Арматура", "🔩"),
    ("construction", "Kafel", "Плитка", "🟦"),
    ("construction", "Laminat", "Ламинат", "🟫"),
    ("construction", "Linoleum", "Линолеум", "🟫"),
    ("construction", "Bo'yoqlar", "Краски", "🎨"),
    ("construction", "Santexnika", "Сантехника", "🚿"),
    ("construction", "Elektr jihozlari", "Электрооборудование", "💡"),
    ("construction", "Eshiklar", "Двери", "🚪"),
    ("construction", "Derazalar", "Окна", "🪟"),
    ("construction", "Tom yopish materiallari", "Кровельные материалы", "🏠"),
    ("construction", "Qurilish asboblari", "Строительные инструменты", "🛠️"),
    # 🏡 Uy uchun mahsulotlar
    ("home_goods", "Gilamlar", "Ковры", "🧶"),
    ("home_goods", "Pardalar", "Шторы", "🪟"),
    ("home_goods", "Lyustralar", "Люстры", "💡"),
    ("home_goods", "Uy dekorlari", "Домашний декор", "🖼️"),
    ("home_goods", "Oshxona buyumlari", "Кухонные принадлежности", "🍴"),
    ("home_goods", "Yotoqxona buyumlari", "Принадлежности для спальни", "🛏️"),
    ("home_goods", "Hammom jihozlari", "Принадлежности для ванной", "🛁"),
    ("home_goods", "Ko'rpa-to'shaklar", "Постельные принадлежности", "🛌"),
    ("home_goods", "Uy tekstili", "Домашний текстиль", "🧵"),
    # ☀️ Quyosh energiyasi uskunalari
    ("solar", "Quyosh panellari", "Солнечные панели", "☀️"),
    ("solar", "Invertorlar", "Инверторы", "🔌"),
    ("solar", "Akkumulyatorlar", "Аккумуляторы", "🔋"),
    ("solar", "Solar lampalar", "Солнечные лампы", "💡"),
    ("solar", "Solar komplektlar", "Солнечные комплекты", "📦"),
    ("solar", "Zaryad stansiyalari", "Зарядные станции", "⚡"),
    # 📹 Xavfsizlik tizimlari
    ("security", "Videokameralar", "Видеокамеры", "📹"),
    ("security", "DVR/NVR qurilmalar", "DVR/NVR устройства", "💽"),
    ("security", "Domofonlar", "Домофоны", "📞"),
    ("security", "Signalizatsiyalar", "Сигнализации", "🚨"),
    ("security", "Smart qulf tizimlari", "Умные замки", "🔐"),
    ("security", "Kirishni nazorat qilish tizimlari", "Системы контроля доступа", "🔑"),
    # 🧸 Bolalar mahsulotlari
    ("kids", "Aravachalar", "Коляски", "👶"),
    ("kids", "Velosipedlar", "Велосипеды", "🚲"),
    ("kids", "Elektromobillar", "Электромобили", "🚗"),
    ("kids", "Karovatlar", "Кроватки", "🛏️"),
    ("kids", "O'yinchoqlar", "Игрушки", "🧸"),
    ("kids", "Maktab buyumlari", "Школьные принадлежности", "🎒"),
    ("kids", "Bolalar mebellari", "Детская мебель", "🪑"),
    ("kids", "Chaqaloq mahsulotlari", "Товары для новорождённых", "🍼"),
    # 📦 Boshqa
    ("other", "Boshqa mahsulotlar", "Прочие товары", "📦"),
]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]


def upgrade() -> None:
    conn = op.get_bind()
    stmt = sa.text(
        "INSERT INTO catalog_categories "
        "(id, slug, name_uz, name_ru, icon_emoji, attribute_schema, identifier_schema, "
        " business_types, sort_order, is_active) "
        "VALUES (:id, :slug, :uz, :ru, :emoji, '[]'::json, '[]'::json, "
        " CAST(:bt AS json), :so, true) "
        "ON CONFLICT (slug) DO NOTHING"
    )
    seen: set[str] = set()
    for i, (biz, uz, ru, emoji) in enumerate(CATEGORIES):
        base = _slug(uz)
        slug = base
        if slug in seen:                       # disambiguate cross-segment repeats
            slug = f"{base}-{biz}"[:48]
        if slug in seen:
            slug = f"{base}-{i}"[:48]
        seen.add(slug)
        conn.execute(stmt, {
            "id": uuid.uuid4(), "slug": slug, "uz": uz, "ru": ru, "emoji": emoji,
            "bt": json.dumps([biz]), "so": i * 10,
        })


def downgrade() -> None:
    # Remove only the seeded slugs (best-effort; manual edits keep their rows).
    pass
