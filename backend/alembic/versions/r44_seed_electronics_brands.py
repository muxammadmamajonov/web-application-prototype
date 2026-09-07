"""Seed brands for the Electronics business type (Round 44)

Each brand is a single row linked to every category it sells in (multi-category
``category_ids``) — the 🔁 entries from the source list are merged here rather
than duplicated. Idempotent on the (category_id, slug) unique constraint.

Revision ID: r44electronicsbrands
Revises: r43seedcategories
Create Date: 2026-05-31 18:00:00+00:00
"""
from __future__ import annotations

import json
import re
import uuid

import sqlalchemy as sa
from alembic import op

revision = "r44electronicsbrands"
down_revision = "r43seedcategories"
branch_labels = None
depends_on = None

# Electronics category slugs (as seeded in r43).
SM, TUG, PL, NB, PC, MON, PRN = (
    "smartfonlar", "tugmali-telefonlar", "planshetlar", "noutbuklar",
    "kompyuterlar", "monitorlar", "printerlar",
)
SW, EAR, SPK, PWR, TACC, CACC, CON, NET = (
    "smart-soatlar", "quloqchinlar", "kolonkalar", "powerbanklar",
    "telefon-aksessuarlari", "kompyuter-aksessuarlari", "o-yin-konsollari",
    "router-va-modemlar",
)

# brand name → categories it belongs to (one row, many categories).
BRANDS: list[tuple[str, list[str]]] = [
    ("Apple", [SM, PL, NB, PC, SW, EAR, TACC]),
    ("Samsung", [SM, PL, NB, MON, SW, EAR, TACC]),
    ("Xiaomi", [SM, PL, SW, EAR, SPK, PWR]),
    ("Redmi", [SM]),
    ("Poco", [SM]),
    ("Honor", [SM, PL, NB, SW, EAR]),
    ("Huawei", [SM, PL, NB, SW, EAR, NET]),
    ("Vivo", [SM]),
    ("Oppo", [SM]),
    ("Tecno", [SM]),
    ("Infinix", [SM]),
    ("Realme", [SM]),
    ("Nokia", [SM, TUG]),
    ("Google Pixel", [SM]),
    ("OnePlus", [SM]),
    ("Nothing", [SM]),
    ("Motorola", [SM]),
    ("ZTE", [SM, NET]),
    ("Sony", [SM, EAR, SPK]),
    ("Artel", [SM, TUG]),
    ("BQ", [TUG]),
    ("Philips", [TUG, MON]),
    ("Maxvi", [TUG]),
    ("F+ (Fly)", [TUG]),
    ("Texno Button", [TUG]),
    ("Novey", [TUG]),
    ("Lenovo", [PL, NB, PC, CON]),
    ("TCL", [PL]),
    ("Blackview", [PL]),
    ("Doogee", [PL]),
    ("Teclast", [PL]),
    ("HP", [NB, PC, MON, PRN, CACC]),
    ("Dell", [NB, PC, MON]),
    ("Asus", [NB, PC, MON, CACC, CON]),
    ("Acer", [NB, PC, MON]),
    ("MSI", [NB, PC, MON]),
    ("Gigabyte", [NB, PC, MON]),
    ("Razer", [NB, CACC]),
    ("Microsoft Surface", [NB]),
    ("Intel NUC", [PC]),
    ("Fujitsu", [PC]),
    ("LG", [MON]),
    ("BenQ", [MON]),
    ("AOC", [MON]),
    ("ViewSonic", [MON]),
    ("Canon", [PRN]),
    ("Epson", [PRN]),
    ("Brother", [PRN]),
    ("Xerox", [PRN]),
    ("Pantum", [PRN]),
    ("Kyocera", [PRN]),
    ("Ricoh", [PRN]),
    ("Amazfit", [SW]),
    ("Garmin", [SW]),
    ("Haylou", [SW]),
    ("Kieslect", [SW]),
    ("Colmi", [SW]),
    ("JBL", [EAR, SPK]),
    ("Marshall", [EAR, SPK]),
    ("Beats", [EAR]),
    ("Anker", [EAR, SPK, PWR, TACC]),
    ("Hoco", [EAR, PWR, TACC]),
    ("Borofone", [EAR, PWR, TACC]),
    ("Baseus", [EAR, PWR, TACC, CACC]),
    ("Remax", [EAR, PWR, TACC]),
    ("Harman Kardon", [SPK]),
    ("Bose", [SPK]),
    ("Tronsmart", [SPK]),
    ("Joyroom", [PWR, TACC]),
    ("Aukey", [PWR]),
    ("Ugreen", [TACC, CACC]),
    ("Logitech", [CACC]),
    ("A4Tech", [CACC]),
    ("HyperX", [CACC]),
    ("Redragon", [CACC]),
    ("Sony PlayStation", [CON]),
    ("Microsoft Xbox", [CON]),
    ("Nintendo", [CON]),
    ("Valve Steam Deck", [CON]),
    ("TP-Link", [NET]),
    ("MikroTik", [NET]),
    ("Ubiquiti", [NET]),
    ("Tenda", [NET]),
    ("Keenetic", [NET]),
    ("D-Link", [NET]),
]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]


def upgrade() -> None:
    conn = op.get_bind()
    # slug → category id
    rows = conn.execute(sa.text("SELECT id, slug FROM catalog_categories")).fetchall()
    cat_id = {r[1]: str(r[0]) for r in rows}

    stmt = sa.text(
        "INSERT INTO catalog_brands (id, category_id, category_ids, slug, name, sort_order, is_active) "
        "VALUES (:id, :cid, CAST(:cids AS json), :slug, :name, :so, true) "
        "ON CONFLICT ON CONSTRAINT uq_brand_category_slug DO NOTHING"
    )
    for i, (name, cat_slugs) in enumerate(BRANDS):
        ids = [cat_id[s] for s in cat_slugs if s in cat_id]
        if not ids:
            continue
        conn.execute(stmt, {
            "id": uuid.uuid4(), "cid": ids[0], "cids": json.dumps(ids),
            "slug": _slug(name), "name": name, "so": i * 10,
        })


def downgrade() -> None:
    pass
