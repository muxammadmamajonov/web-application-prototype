"""Seed catalog models + variant templates for the highest-volume categories (MUDD-113 B)

r44–r54 seeded BRANDS across every category (electronics, appliances,
furniture, auto, clothing, jewelry, construction, home goods, solar,
security, kids) — but zero MODELS existed anywhere in the catalog before this
migration; ``catalog_models`` had DDL and nothing else.

Scope: real, currently-relevant model lines for the six categories where a
named "model" is both unambiguous and independently verifiable — phones,
tablets, laptops, TVs, cars, motorcycles — restricted to brands r44/r47
already seeded for that category (`category_ids` containment, same lookup
the API uses). Appliance/furniture/jewelry/construction "models" are, in
practice, SKUs distinguished by the attribute_schema (volume, size, material)
already covers; inventing named model lines there would be exactly the "soxta
identifikator" the owner asked this pass to avoid, so it is left for a
follow-up once real supplier catalogs are available to seed from.

``attributes_template`` only lists the axes each category's r55
attribute_schema marks ``variant=True`` (see r55's per-category variant
lists) — a model does not need to pre-populate non-variant descriptive specs.
Motorcycle/scooter ``engine_cc`` and screen sizes reflect the trim each brand
actually ships, not an exhaustive per-VIN option list; tenants can still add
their own values through the "propose a model" flow.

Idempotent: the table's existing ``uq_model_brand_slug_tenant`` constraint
covers ``(brand_id, slug, tenant_id)``, but every row this migration inserts
has ``tenant_id IS NULL`` — and Postgres unique constraints treat every NULL
as distinct from every other NULL, so that constraint never actually fires
for these rows (proven by re-running this migration's ``upgrade()`` against
an already-seeded table: row count doubled instead of staying flat). The fix
is a dedicated partial unique index scoped to the global rows,
``uq_catalog_models_brand_slug_global`` on ``(brand_id, slug) WHERE
tenant_id IS NULL``, which ``ON CONFLICT`` targets explicitly so a second run
adds nothing.

Revision ID: r97catmodels
Revises: r96catidschema
Create Date: 2026-08-29 14:00:00+00:00
"""
from __future__ import annotations

import json
import uuid

import sqlalchemy as sa

from alembic import op

revision = "r97catmodels"
down_revision = "r96catidschema"
branch_labels = None
depends_on = None


# (category_slug, brand_slug, model_slug, name, release_year, attributes_template)
MODELS: list[tuple[str, str, str, str, int, dict]] = [
    # ── 📱 Smartfonlar ──────────────────────────────────────────────────────
    ("smartfonlar", "apple", "iphone-13", "iPhone 13", 2021,
     {"color": ["black", "white", "blue", "red", "pink"], "storage": [128, 256, 512]}),
    ("smartfonlar", "apple", "iphone-14", "iPhone 14", 2022,
     {"color": ["black", "white", "blue", "red", "purple", "yellow"], "storage": [128, 256, 512]}),
    ("smartfonlar", "apple", "iphone-15", "iPhone 15", 2023,
     {"color": ["black", "blue", "green", "yellow", "pink"], "storage": [128, 256, 512]}),
    ("smartfonlar", "apple", "iphone-15-pro", "iPhone 15 Pro", 2023,
     {"color": ["black", "white", "blue", "navy"], "storage": [128, 256, 512, 1024]}),
    ("smartfonlar", "apple", "iphone-15-pro-max", "iPhone 15 Pro Max", 2023,
     {"color": ["black", "white", "blue", "navy"], "storage": [256, 512, 1024]}),
    ("smartfonlar", "apple", "iphone-16", "iPhone 16", 2024,
     {"color": ["black", "white", "pink", "turquoise", "yellow"], "storage": [128, 256, 512]}),
    ("smartfonlar", "samsung", "galaxy-a14", "Galaxy A14", 2023,
     {"color": ["black", "silver", "green"], "storage": [64, 128]}),
    ("smartfonlar", "samsung", "galaxy-a54", "Galaxy A54", 2023,
     {"color": ["black", "white", "green", "purple"], "storage": [128, 256]}),
    ("smartfonlar", "samsung", "galaxy-s23", "Galaxy S23", 2023,
     {"color": ["black", "green", "purple", "white"], "storage": [128, 256]}),
    ("smartfonlar", "samsung", "galaxy-s23-ultra", "Galaxy S23 Ultra", 2023,
     {"color": ["black", "green", "purple", "beige"], "storage": [256, 512, 1024]}),
    ("smartfonlar", "samsung", "galaxy-s24", "Galaxy S24", 2024,
     {"color": ["black", "gray", "yellow", "purple"], "storage": [128, 256]}),
    ("smartfonlar", "samsung", "galaxy-s24-ultra", "Galaxy S24 Ultra", 2024,
     {"color": ["black", "gray", "yellow", "purple"], "storage": [256, 512, 1024]}),
    ("smartfonlar", "samsung", "galaxy-z-flip5", "Galaxy Z Flip5", 2023,
     {"color": ["graphite", "gray", "gold", "green"], "storage": [256, 512]}),
    ("smartfonlar", "xiaomi", "xiaomi-13", "Xiaomi 13", 2022,
     {"color": ["black", "white", "green"], "storage": [128, 256]}),
    ("smartfonlar", "xiaomi", "xiaomi-13t", "Xiaomi 13T", 2023,
     {"color": ["black", "blue", "gold"], "storage": [256]}),
    ("smartfonlar", "xiaomi", "xiaomi-14", "Xiaomi 14", 2023,
     {"color": ["black", "white", "green"], "storage": [256, 512]}),
    ("smartfonlar", "redmi", "redmi-note-12", "Redmi Note 12", 2023,
     {"color": ["black", "blue", "green"], "storage": [128, 256]}),
    ("smartfonlar", "redmi", "redmi-note-13", "Redmi Note 13", 2024,
     {"color": ["black", "green", "purple"], "storage": [128, 256]}),
    ("smartfonlar", "redmi", "redmi-a3", "Redmi A3", 2024,
     {"color": ["black", "green", "blue"], "storage": [64, 128]}),
    ("smartfonlar", "poco", "poco-x6", "Poco X6", 2024,
     {"color": ["black", "blue", "yellow"], "storage": [256]}),
    ("smartfonlar", "poco", "poco-x6-pro", "Poco X6 Pro", 2024,
     {"color": ["black", "gray", "yellow"], "storage": [256, 512]}),
    ("smartfonlar", "honor", "honor-x9b", "Honor X9b", 2024,
     {"color": ["black", "green", "silver"], "storage": [256]}),
    ("smartfonlar", "honor", "honor-magic6-lite", "Honor Magic6 Lite", 2024,
     {"color": ["black", "green"], "storage": [256]}),
    ("smartfonlar", "huawei", "huawei-nova-11", "Huawei Nova 11", 2023,
     {"color": ["black", "green", "gold"], "storage": [128, 256]}),
    ("smartfonlar", "huawei", "huawei-p60", "Huawei P60", 2023,
     {"color": ["black", "white", "green"], "storage": [256]}),
    ("smartfonlar", "infinix", "infinix-note-30", "Infinix Note 30", 2023,
     {"color": ["black", "silver", "green"], "storage": [128, 256]}),
    ("smartfonlar", "infinix", "infinix-hot-40", "Infinix Hot 40", 2024,
     {"color": ["black", "blue", "green"], "storage": [128]}),
    ("smartfonlar", "tecno", "tecno-spark-20", "Tecno Spark 20", 2023,
     {"color": ["black", "blue"], "storage": [128]}),
    ("smartfonlar", "tecno", "tecno-camon-20", "Tecno Camon 20", 2023,
     {"color": ["black", "gold"], "storage": [128, 256]}),
    ("smartfonlar", "realme", "realme-11", "Realme 11", 2023,
     {"color": ["black", "gold"], "storage": [128, 256]}),
    ("smartfonlar", "realme", "realme-c55", "Realme C55", 2023,
     {"color": ["black", "gold"], "storage": [128]}),
    ("smartfonlar", "oneplus", "oneplus-nord-3", "OnePlus Nord 3", 2023,
     {"color": ["black", "green"], "storage": [128, 256]}),
    ("smartfonlar", "vivo", "vivo-y36", "Vivo Y36", 2023,
     {"color": ["black", "blue"], "storage": [128, 256]}),
    ("smartfonlar", "oppo", "oppo-a78", "Oppo A78", 2023,
     {"color": ["black", "blue"], "storage": [128, 256]}),
    ("smartfonlar", "nothing", "nothing-phone-2a", "Nothing Phone (2a)", 2024,
     {"color": ["black", "white"], "storage": [128, 256]}),
    ("smartfonlar", "google-pixel", "pixel-8", "Google Pixel 8", 2023,
     {"color": ["black", "white", "pink"], "storage": [128, 256]}),
    ("smartfonlar", "motorola", "moto-edge-40", "Motorola Edge 40", 2023,
     {"color": ["black", "green"], "storage": [256]}),
    ("smartfonlar", "sony", "xperia-10-v", "Sony Xperia 10 V", 2023,
     {"color": ["black", "white", "sky-blue"], "storage": [128]}),
    ("smartfonlar", "zte", "zte-blade-v50", "ZTE Blade V50", 2024,
     {"color": ["black", "blue"], "storage": [128]}),
    ("smartfonlar", "nokia", "nokia-g21", "Nokia G21", 2022,
     {"color": ["black", "blue"], "storage": [64, 128]}),
    ("smartfonlar", "artel", "artel-a55", "Artel A55", 2023,
     {"color": ["black", "blue"], "storage": [64, 128]}),

    # ── 📲 Planshetlar ──────────────────────────────────────────────────────
    ("planshetlar", "apple", "ipad-10th-gen", "iPad (10th gen)", 2022,
     {"color": ["blue", "pink", "yellow", "silver"], "storage": [64, 256], "ram": [4]}),
    ("planshetlar", "apple", "ipad-air-5", "iPad Air (5th gen)", 2022,
     {"color": ["silver", "gray", "blue", "purple"], "storage": [64, 256], "ram": [8]}),
    ("planshetlar", "apple", "ipad-pro-11", "iPad Pro 11\"", 2022,
     {"color": ["gray", "silver"], "storage": [128, 256, 512], "ram": [8]}),
    ("planshetlar", "samsung", "galaxy-tab-a9", "Galaxy Tab A9", 2023,
     {"color": ["gray", "silver", "green"], "storage": [64, 128], "ram": [4, 8]}),
    ("planshetlar", "samsung", "galaxy-tab-s9", "Galaxy Tab S9", 2023,
     {"color": ["gray", "beige"], "storage": [128, 256], "ram": [8, 12]}),
    ("planshetlar", "huawei", "matepad-11", "Huawei MatePad 11", 2022,
     {"color": ["gray", "green"], "storage": [128], "ram": [6, 8]}),
    ("planshetlar", "honor", "honor-pad-9", "Honor Pad 9", 2023,
     {"color": ["gray"], "storage": [128], "ram": [8]}),
    ("planshetlar", "xiaomi", "redmi-pad-se", "Redmi Pad SE", 2023,
     {"color": ["gray", "blue"], "storage": [128], "ram": [4, 6]}),
    ("planshetlar", "lenovo", "tab-m10", "Lenovo Tab M10", 2022,
     {"color": ["gray", "blue"], "storage": [64, 128], "ram": [4]}),
    ("planshetlar", "tcl", "tcl-tab-10", "TCL Tab 10", 2022,
     {"color": ["black"], "storage": [64], "ram": [4]}),
    ("planshetlar", "teclast", "teclast-p20hd", "Teclast P20HD", 2022,
     {"color": ["gray"], "storage": [64], "ram": [4]}),
    ("planshetlar", "blackview", "blackview-tab-8", "Blackview Tab 8", 2022,
     {"color": ["gray"], "storage": [64], "ram": [4]}),
    ("planshetlar", "doogee", "doogee-t20", "Doogee T20", 2022,
     {"color": ["gray"], "storage": [128], "ram": [8]}),

    # ── 💻 Noutbuklar ───────────────────────────────────────────────────────
    ("noutbuklar", "apple", "macbook-air-m2", "MacBook Air M2", 2022,
     {"color": ["silver", "space-gray", "gold"], "cpu": ["apple"], "ram": [8, 16], "ssd": [256, 512]}),
    ("noutbuklar", "samsung", "galaxy-book3", "Galaxy Book3", 2023,
     {"color": ["silver", "graphite"], "cpu": ["i5", "i7"], "ram": [8, 16], "ssd": [256, 512]}),
    ("noutbuklar", "lenovo", "ideapad-3", "IdeaPad 3", 2023,
     {"color": ["black", "silver"], "cpu": ["i3", "i5", "r5"], "ram": [8], "ssd": [256, 512]}),
    ("noutbuklar", "lenovo", "thinkpad-e14", "ThinkPad E14", 2023,
     {"color": ["black"], "cpu": ["i5", "i7"], "ram": [8, 16], "ssd": [256, 512]}),
    ("noutbuklar", "hp", "pavilion-15", "Pavilion 15", 2023,
     {"color": ["silver", "black"], "cpu": ["i5", "i7", "r5"], "ram": [8, 16], "ssd": [256, 512]}),
    ("noutbuklar", "hp", "victus-16", "Victus 16", 2023,
     {"color": ["black"], "cpu": ["i5", "i7"], "ram": [16], "ssd": [512]}),
    ("noutbuklar", "dell", "inspiron-15", "Inspiron 15", 2023,
     {"color": ["silver", "black"], "cpu": ["i5", "i7"], "ram": [8, 16], "ssd": [256, 512]}),
    ("noutbuklar", "asus", "vivobook-15", "VivoBook 15", 2023,
     {"color": ["black", "silver", "blue"], "cpu": ["i3", "i5", "r5"], "ram": [8], "ssd": [256, 512]}),
    ("noutbuklar", "asus", "rog-strix-g16", "ROG Strix G16", 2023,
     {"color": ["black"], "cpu": ["i7", "i9"], "ram": [16], "ssd": [512, 1024]}),
    ("noutbuklar", "acer", "aspire-5", "Aspire 5", 2023,
     {"color": ["silver", "black"], "cpu": ["i3", "i5"], "ram": [8], "ssd": [256, 512]}),
    ("noutbuklar", "msi", "modern-14", "Modern 14", 2023,
     {"color": ["black"], "cpu": ["i5", "i7"], "ram": [8, 16], "ssd": [512]}),
    ("noutbuklar", "gigabyte", "gaming-a16", "Gaming A16", 2023,
     {"color": ["black"], "cpu": ["i7", "r7"], "ram": [16], "ssd": [512]}),
    ("noutbuklar", "huawei", "matebook-d14", "MateBook D14", 2023,
     {"color": ["silver", "gray"], "cpu": ["i5"], "ram": [8, 16], "ssd": [512]}),
    ("noutbuklar", "honor", "magicbook-14", "MagicBook 14", 2023,
     {"color": ["silver", "gray"], "cpu": ["i5", "r5"], "ram": [8, 16], "ssd": [512]}),
    ("noutbuklar", "microsoft-surface", "surface-laptop-5", "Surface Laptop 5", 2022,
     {"color": ["black", "silver"], "cpu": ["i5", "i7"], "ram": [8, 16], "ssd": [256, 512]}),
    ("noutbuklar", "razer", "razer-blade-14", "Razer Blade 14", 2023,
     {"color": ["black"], "cpu": ["r7"], "ram": [16], "ssd": [1024]}),

    # ── 📺 Televizorlar ─────────────────────────────────────────────────────
    ("televizorlar", "samsung", "crystal-uhd-cu7000", "Crystal UHD CU7000", 2023,
     {"size_in": [43, 50, 55, 65], "resolution": ["4k"]}),
    ("televizorlar", "samsung", "the-frame", "The Frame", 2023,
     {"size_in": [43, 50, 55], "resolution": ["4k"]}),
    ("televizorlar", "lg", "lg-uq7500", "LG UQ7500", 2023,
     {"size_in": [43, 50, 55, 65], "resolution": ["4k"]}),
    ("televizorlar", "lg", "lg-oled-b3", "LG OLED B3", 2023,
     {"size_in": [55, 65], "resolution": ["4k"]}),
    ("televizorlar", "xiaomi", "xiaomi-tv-a2", "Xiaomi TV A2", 2023,
     {"size_in": [32, 43, 50, 55], "resolution": ["hd", "fhd", "4k"]}),
    ("televizorlar", "tcl", "tcl-p635", "TCL P635", 2023,
     {"size_in": [43, 50, 55, 65], "resolution": ["4k"]}),
    ("televizorlar", "hisense", "hisense-a6k", "Hisense A6K", 2023,
     {"size_in": [43, 50, 55], "resolution": ["4k"]}),
    ("televizorlar", "sony", "sony-bravia-x75k", "Sony Bravia X75K", 2023,
     {"size_in": [43, 55, 65], "resolution": ["4k"]}),
    ("televizorlar", "toshiba", "toshiba-c350", "Toshiba C350", 2023,
     {"size_in": [43, 50], "resolution": ["4k"]}),
    ("televizorlar", "artel", "artel-ua43h", "Artel UA43H", 2023,
     {"size_in": [32, 43], "resolution": ["hd", "fhd"]}),
    ("televizorlar", "avalon", "avalon-le32", "Avalon LE32", 2023,
     {"size_in": [32], "resolution": ["hd"]}),
    ("televizorlar", "premier", "premier-ps43", "Premier PS43", 2023,
     {"size_in": [32, 43], "resolution": ["hd", "fhd"]}),
    ("televizorlar", "roison", "roison-rs43", "Roison RS43", 2023,
     {"size_in": [32, 43], "resolution": ["hd", "fhd"]}),
    ("televizorlar", "shivaki", "shivaki-us43", "Shivaki US43", 2023,
     {"size_in": [32, 43], "resolution": ["hd", "fhd"]}),
    ("televizorlar", "philips", "philips-43pfs", "Philips 43PFS", 2023,
     {"size_in": [43, 50], "resolution": ["fhd", "4k"]}),

    # ── 🚗 Avtomobillar ─────────────────────────────────────────────────────
    ("avtomobillar", "chevrolet", "cobalt", "Chevrolet Cobalt", 2016,
     {"color": ["white", "black", "silver", "red"], "fuel": ["petrol"], "transmission": ["manual", "auto"]}),
    ("avtomobillar", "chevrolet", "nexia3", "Chevrolet Nexia 3", 2015,
     {"color": ["white", "black", "silver"], "fuel": ["petrol"], "transmission": ["manual"]}),
    ("avtomobillar", "chevrolet", "malibu2", "Chevrolet Malibu 2", 2017,
     {"color": ["white", "black", "silver", "blue"], "fuel": ["petrol"], "transmission": ["auto"]}),
    ("avtomobillar", "chevrolet", "tracker", "Chevrolet Tracker", 2021,
     {"color": ["white", "black", "gray"], "fuel": ["petrol"], "transmission": ["auto"]}),
    ("avtomobillar", "chevrolet", "onix", "Chevrolet Onix", 2020,
     {"color": ["white", "black", "red"], "fuel": ["petrol"], "transmission": ["auto", "manual"]}),
    ("avtomobillar", "chevrolet", "damas", "Chevrolet Damas", 1999,
     {"color": ["white"], "fuel": ["petrol"], "transmission": ["manual"]}),
    ("avtomobillar", "chevrolet", "labo", "Chevrolet Labo", 2015,
     {"color": ["white"], "fuel": ["petrol"], "transmission": ["manual"]}),
    ("avtomobillar", "daewoo", "matiz", "Daewoo Matiz", 2002,
     {"color": ["white", "silver", "blue"], "fuel": ["petrol"], "transmission": ["manual"]}),
    ("avtomobillar", "ravon", "r2", "Ravon R2", 2016,
     {"color": ["white", "silver"], "fuel": ["petrol"], "transmission": ["manual"]}),
    ("avtomobillar", "ravon", "gentra", "Ravon Gentra", 2017,
     {"color": ["white", "black", "silver"], "fuel": ["petrol"], "transmission": ["auto", "manual"]}),
    ("avtomobillar", "chery", "tiggo-7-pro", "Chery Tiggo 7 Pro", 2022,
     {"color": ["white", "black", "gray", "red"], "fuel": ["petrol"], "transmission": ["auto"]}),
    ("avtomobillar", "chery", "tiggo-8-pro", "Chery Tiggo 8 Pro", 2022,
     {"color": ["white", "black"], "fuel": ["petrol"], "transmission": ["auto"]}),
    ("avtomobillar", "chery", "arrizo-5", "Chery Arrizo 5", 2021,
     {"color": ["white", "black"], "fuel": ["petrol"], "transmission": ["auto"]}),
    ("avtomobillar", "haval", "jolion", "Haval Jolion", 2022,
     {"color": ["white", "black", "gray", "orange"], "fuel": ["petrol"], "transmission": ["auto"]}),
    ("avtomobillar", "haval", "f7", "Haval F7", 2021,
     {"color": ["white", "black"], "fuel": ["petrol"], "transmission": ["auto"]}),
    ("avtomobillar", "byd", "song-plus", "BYD Song Plus", 2023,
     {"color": ["white", "black", "blue"], "fuel": ["electric"], "transmission": ["auto"]}),
    ("avtomobillar", "byd", "atto-3", "BYD Atto 3", 2023,
     {"color": ["white", "black", "gray"], "fuel": ["electric"], "transmission": ["auto"]}),
    ("avtomobillar", "changan", "cs35plus", "Changan CS35 Plus", 2022,
     {"color": ["white", "black"], "fuel": ["petrol"], "transmission": ["auto"]}),
    ("avtomobillar", "toyota", "camry", "Toyota Camry", 2018,
     {"color": ["white", "black", "silver"], "fuel": ["petrol", "hybrid"], "transmission": ["auto"]}),
    ("avtomobillar", "toyota", "corolla", "Toyota Corolla", 2019,
     {"color": ["white", "black", "silver"], "fuel": ["petrol"], "transmission": ["auto"]}),
    ("avtomobillar", "toyota", "land-cruiser-prado", "Toyota Land Cruiser Prado", 2020,
     {"color": ["white", "black"], "fuel": ["diesel", "petrol"], "transmission": ["auto"]}),
    ("avtomobillar", "hyundai", "elantra", "Hyundai Elantra", 2020,
     {"color": ["white", "black", "silver"], "fuel": ["petrol"], "transmission": ["auto"]}),
    ("avtomobillar", "hyundai", "tucson", "Hyundai Tucson", 2021,
     {"color": ["white", "black"], "fuel": ["petrol"], "transmission": ["auto"]}),
    ("avtomobillar", "kia", "k5", "Kia K5", 2021,
     {"color": ["white", "black", "silver"], "fuel": ["petrol"], "transmission": ["auto"]}),
    ("avtomobillar", "kia", "sportage", "Kia Sportage", 2022,
     {"color": ["white", "black"], "fuel": ["petrol"], "transmission": ["auto"]}),

    # ── 🏍️ Mototsikllar ─────────────────────────────────────────────────────
    ("mototsikllar", "honda", "cb190r", "Honda CB190R", 2020,
     {"color": ["red", "black", "white"], "engine_cc": [184]}),
    ("mototsikllar", "honda", "cbr150r", "Honda CBR150R", 2021,
     {"color": ["red", "black"], "engine_cc": [150]}),
    ("mototsikllar", "yamaha", "ybr125", "Yamaha YBR125", 2019,
     {"color": ["blue", "black"], "engine_cc": [125]}),
    ("mototsikllar", "yamaha", "mt-15", "Yamaha MT-15", 2021,
     {"color": ["blue", "black"], "engine_cc": [155]}),
    ("mototsikllar", "suzuki", "gsx-r150", "Suzuki GSX-R150", 2020,
     {"color": ["blue", "black"], "engine_cc": [147]}),
    ("mototsikllar", "bajaj", "boxer-150", "Bajaj Boxer 150", 2018,
     {"color": ["black", "blue"], "engine_cc": [150]}),
    ("mototsikllar", "bajaj", "pulsar-150", "Bajaj Pulsar 150", 2019,
     {"color": ["black", "red"], "engine_cc": [150]}),
    ("mototsikllar", "lifan", "kpr150", "Lifan KPR150", 2019,
     {"color": ["red", "black"], "engine_cc": [150]}),
    ("mototsikllar", "loncin", "lx200", "Loncin LX200", 2019,
     {"color": ["black"], "engine_cc": [200]}),
    ("mototsikllar", "irbis", "ttr125", "Irbis TTR125", 2019,
     {"color": ["green"], "engine_cc": [125]}),
    ("mototsikllar", "racer", "rc200", "Racer RC200", 2019,
     {"color": ["black", "orange"], "engine_cc": [200]}),
    ("mototsikllar", "motoland", "xr250", "Motoland XR250", 2020,
     {"color": ["orange", "black"], "engine_cc": [250]}),
    ("mototsikllar", "shineray", "xy200", "Shineray XY200", 2019,
     {"color": ["black"], "engine_cc": [200]}),
    ("mototsikllar", "zongshen", "zs200", "Zongshen ZS200", 2019,
     {"color": ["black", "red"], "engine_cc": [200]}),
    ("mototsikllar", "benda", "ld125", "Benda LD125", 2020,
     {"color": ["white", "black"], "engine_cc": [125]}),
]


def upgrade() -> None:
    conn = op.get_bind()

    # See module docstring: uq_model_brand_slug_tenant does not fire for
    # tenant_id IS NULL rows, so ON CONFLICT needs a partial index that
    # actually treats those rows as comparable to each other.
    conn.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_models_brand_slug_global "
        "ON catalog_models (brand_id, slug) WHERE tenant_id IS NULL"
    ))

    cat_slugs = sorted({m[0] for m in MODELS})
    cat_rows = conn.execute(
        sa.text("SELECT id, slug FROM catalog_categories WHERE slug = ANY(:slugs)"),
        {"slugs": cat_slugs},
    ).fetchall()
    cat_ids = {row.slug: row.id for row in cat_rows}
    missing_cats = set(cat_slugs) - set(cat_ids)
    if missing_cats:
        raise RuntimeError(f"r97catmodels: unknown category slugs {sorted(missing_cats)}")

    brand_stmt = sa.text(
        "SELECT id FROM catalog_brands WHERE slug = :brand_slug "
        "AND category_ids::jsonb @> to_jsonb(CAST(:cat_id AS text))"
    )
    insert_stmt = sa.text(
        "INSERT INTO catalog_models "
        "(id, brand_id, category_id, slug, name, release_year, attributes_template, "
        " sort_order, is_active, status, tenant_id, created_at, updated_at) "
        "VALUES (:id, :brand_id, :category_id, :slug, :name, :release_year, "
        " CAST(:attrs AS json), 100, true, 'global', NULL, now(), now()) "
        "ON CONFLICT (brand_id, slug) WHERE tenant_id IS NULL DO NOTHING"
    )

    inserted = 0
    for cat_slug, brand_slug, model_slug, name, release_year, attrs in MODELS:
        cat_id = cat_ids[cat_slug]
        brand_row = conn.execute(brand_stmt, {"brand_slug": brand_slug, "cat_id": str(cat_id)}).first()
        if brand_row is None:
            raise RuntimeError(
                f"r97catmodels: no brand {brand_slug!r} linked to category {cat_slug!r} — "
                "is the matching r44–r54 brand seed applied?"
            )
        result = conn.execute(insert_stmt, {
            "id": str(uuid.uuid4()),
            "brand_id": str(brand_row.id),
            "category_id": str(cat_id),
            "slug": model_slug,
            "name": name,
            "release_year": release_year,
            "attrs": json.dumps(attrs),
        })
        inserted += result.rowcount

    print(f"[r97catmodels] {inserted} models inserted ({len(MODELS)} defined, "
          f"{len(MODELS) - inserted} already present)")


def downgrade() -> None:
    conn = op.get_bind()
    stmt = sa.text(
        "DELETE FROM catalog_models m USING catalog_brands b "
        "WHERE m.brand_id = b.id AND m.tenant_id IS NULL AND b.slug = :brand_slug AND m.slug = :model_slug"
    )
    for _, brand_slug, model_slug, *_ in MODELS:
        conn.execute(stmt, {"brand_slug": brand_slug, "model_slug": model_slug})
    conn.execute(sa.text("DROP INDEX IF EXISTS uq_catalog_models_brand_slug_global"))
