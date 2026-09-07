"""Expand the colour palette (Round 40)

Adds the rest of a common product-colour set (uz/ru names + hex). Idempotent:
``ON CONFLICT (slug) DO NOTHING`` skips colours already seeded in r38.

Revision ID: r40morecolors
Revises: r39planoverage
Create Date: 2026-05-31 13:00:00+00:00
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "r40morecolors"
down_revision = "r39planoverage"
branch_labels = None
depends_on = None

# (slug, name_uz, name_ru, hex)
COLORS = [
    ("jet-black", "Tim qora", "Смоляной чёрный", "#0A0A0A"),
    ("matte-black", "Mat qora", "Матовый чёрный", "#1C1C1C"),
    ("midnight-black", "Yarim tungi qora", "Полуночный чёрный", "#121212"),
    ("space-black", "Kosmik qora", "Космический чёрный", "#1A1A1A"),
    ("onyx-black", "Oniks qora", "Ониксовый чёрный", "#353839"),
    ("charcoal", "Ko'mir", "Угольный", "#36454F"),
    ("titanium-black", "Titan qora", "Титановый чёрный", "#2E2E2E"),
    ("phantom-black", "Fantom qora", "Фантомный чёрный", "#101820"),
    ("pearl-white", "Marvarid oq", "Жемчужно-белый", "#F8F6F0"),
    ("snow-white", "Qor oq", "Белоснежный", "#FFFAFA"),
    ("ceramic-white", "Keramik oq", "Керамический белый", "#F5F5F5"),
    ("ivory-white", "Fil suyagi", "Слоновая кость", "#FFFFF0"),
    ("cream-white", "Krem oq", "Кремовый", "#FFFDD0"),
    ("frost-white", "Sovuq oq", "Морозный белый", "#F4F8FF"),
    ("arctic-white", "Arktika oq", "Арктический белый", "#F8FAFC"),
    ("silk-white", "Ipak oq", "Шёлковый белый", "#FAF9F6"),
    ("moon-white", "Oy oq", "Лунный белый", "#F0F4F8"),
    ("platinum", "Platina", "Платиновый", "#E5E4E2"),
    ("titanium", "Titan", "Титановый", "#878681"),
    ("natural-titanium", "Tabiiy titan", "Натуральный титан", "#B8B4AE"),
    ("silver-titanium", "Kumush titan", "Серебристый титан", "#C5C5C5"),
    ("starlight", "Yulduz nuri", "Звёздный свет", "#F5E6C8"),
    ("steel-gray", "Po'lat kulrang", "Стальной серый", "#71797E"),
    ("gunmetal", "Qurol metali", "Оружейный металл", "#2A3439"),
    ("chrome-silver", "Xrom kumush", "Хромированное серебро", "#D7D7D7"),
    ("pink-gold", "Pushti oltin rang", "Розово-золотой", "#E6BEAE"),
    ("champagne-gold", "Shampan oltin", "Шампань-золото", "#F7E7CE"),
    ("desert-gold", "Sahro oltin", "Песочное золото", "#D6B370"),
    ("bronze-gold", "Bronza oltin", "Бронзовое золото", "#CD7F32"),
    ("titanium-gold", "Titan oltin", "Титановое золото", "#CBA135"),
    ("antique-gold", "Antiqa oltin", "Античное золото", "#CFB53B"),
    ("sand-gold", "Qum oltin", "Песочно-золотой", "#D8C3A5"),
    ("luxury-gold", "Hashamatli oltin", "Роскошное золото", "#D4AF37"),
    ("sierra-blue", "Sierra ko'k", "Сьерра-синий", "#9BB5CE"),
    ("pacific-blue", "Tinch okean ko'k", "Тихоокеанский синий", "#1B4D72"),
    ("alpine-blue", "Alp ko'k", "Альпийский синий", "#6B8BA4"),
    ("ocean-blue", "Okean ko'k", "Океанский синий", "#0077BE"),
    ("navy-blue", "Dengiz ko'k", "Морской синий", "#000080"),
    ("midnight-blue", "Yarim tungi ko'k", "Полуночный синий", "#191970"),
    ("cobalt-blue", "Kobalt ko'k", "Кобальтовый синий", "#0047AB"),
    ("ice-blue", "Muz ko'k", "Ледяной синий", "#D6F0FF"),
    ("alpine-green", "Alp yashil", "Альпийский зелёный", "#4F7942"),
    ("forest-green", "O'rmon yashil", "Лесной зелёный", "#228B22"),
    ("olive-green", "Zaytun yashil", "Оливковый", "#708238"),
    ("mint-green", "Yalpiz yashil", "Мятный", "#98FF98"),
    ("sage-green", "Marmarak yashil", "Шалфейный зелёный", "#9CAF88"),
    ("emerald-green", "Zumrad yashil", "Изумрудный", "#50C878"),
    ("pine-green", "Qarag'ay yashil", "Сосновый зелёный", "#01796F"),
    ("moss-green", "Yo'sin yashil", "Моховой зелёный", "#8A9A5B"),
    ("military-green", "Harbiy yashil", "Военный зелёный", "#4B5320"),
    ("product-red", "Product Red", "Product Red", "#C8102E"),
    ("crimson-red", "Qirmizi", "Малиновый красный", "#DC143C"),
    ("burgundy", "Burgundiya", "Бордовый", "#800020"),
    ("wine-red", "Vino qizil", "Винный", "#722F37"),
    ("cherry-red", "Gilos qizil", "Вишнёвый", "#D2042D"),
    ("ruby-red", "Yoqut qizil", "Рубиновый", "#E0115F"),
    ("coral-red", "Marjon qizil", "Коралловый красный", "#FF4040"),
    ("sunset-red", "Quyosh botishi", "Закатный красный", "#FD5E53"),
    ("brick-red", "G'isht qizil", "Кирпичный", "#CB4154"),
    ("deep-purple", "To'q binafsha", "Тёмно-фиолетовый", "#5B21B6"),
    ("lavender-purple", "Lavanda binafsha", "Лавандовый", "#B57EDC"),
    ("lilac", "Nilufar", "Сиреневый", "#C8A2C8"),
    ("violet", "Yorqin binafsha", "Фиолетовый", "#8F00FF"),
    ("orchid-purple", "Orxideya binafsha", "Орхидейный", "#DA70D6"),
    ("amethyst", "Ametist", "Аметистовый", "#9966CC"),
    ("plum", "Olxo'ri", "Сливовый", "#8E4585"),
    ("mystic-purple", "Sirli binafsha", "Мистический фиолетовый", "#7F5AF0"),
    ("royal-purple", "Shohona binafsha", "Королевский фиолетовый", "#7851A9"),
    ("blush-pink", "Nozik pushti", "Румяный розовый", "#F4C2C2"),
    ("soft-pink", "Yumshoq pushti", "Нежно-розовый", "#F8BBD0"),
    ("sakura-pink", "Sakura pushti", "Сакура розовый", "#FFB7C5"),
    ("dusty-pink", "Changli pushti", "Пыльно-розовый", "#D8A7B1"),
    ("coral-pink", "Marjon pushti", "Коралловый розовый", "#F88379"),
    ("peach-pink", "Shaftoli pushti", "Персиково-розовый", "#FFDAB9"),
    ("rose-pink", "Atirgul pushti", "Розовый", "#FF66CC"),
    ("bubblegum-pink", "Saqich pushti", "Розовая жвачка", "#FF69B4"),
    ("quartz-pink", "Kvarts pushti", "Кварцевый розовый", "#F7CAC9"),
    ("sand", "Qum", "Песочный", "#C2B280"),
    ("desert-sand", "Sahro qum", "Песок пустыни", "#EDC9AF"),
    ("clay", "Loy", "Глиняный", "#B66A50"),
    ("mocha", "Mokko", "Мокко", "#967969"),
    ("coffee-brown", "Qahva jigarrang", "Кофейный", "#6F4E37"),
    ("chocolate-brown", "Shokolad jigarrang", "Шоколадный", "#7B3F00"),
    ("camel", "Tuya rang", "Верблюжий", "#C19A6B"),
    ("stone", "Tosh rang", "Каменный", "#928E85"),
    ("natural", "Tabiiy", "Натуральный", "#D6D2C4"),
]


def upgrade() -> None:
    conn = op.get_bind()
    stmt = sa.text(
        "INSERT INTO catalog_colors (id, slug, name_uz, name_ru, hex, sort_order, is_active) "
        "VALUES (:id, :slug, :name_uz, :name_ru, :hex, :sort_order, true) "
        "ON CONFLICT (slug) DO NOTHING"
    )
    for i, (slug, uz, ru, hx) in enumerate(COLORS):
        conn.execute(stmt, {
            "id": uuid.uuid4(), "slug": slug, "name_uz": uz, "name_ru": ru,
            "hex": hx, "sort_order": 200 + i,
        })


def downgrade() -> None:
    conn = op.get_bind()
    slugs = tuple(c[0] for c in COLORS)
    conn.execute(
        sa.text("DELETE FROM catalog_colors WHERE slug IN :slugs").bindparams(
            sa.bindparam("slugs", expanding=True),
        ),
        {"slugs": list(slugs)},
    )
