"""Seed brands for the Maishiy texnika (appliances) business type (Round 45)

Brands shared with Electronics (Samsung, LG, Xiaomi, Sony, Philips, TCL,
Toshiba, Panasonic, Artel) are MERGED into the existing brand's category_ids
(matched by slug) — one brand, many categories. New brands are inserted.
Idempotent.

Revision ID: r45applianbrands
Revises: r44electronicsbrands
Create Date: 2026-05-31 19:00:00+00:00
"""
from __future__ import annotations

import json
import re
import uuid

import sqlalchemy as sa
from alembic import op

revision = "r45applianbrands"
down_revision = "r44electronicsbrands"
branch_labels = None
depends_on = None

# Appliance category slugs (as seeded in r43).
MUZ, KIR, GAZ, ELP, DUX, MIK = (
    "muzlatgichlar", "kir-yuvish-mashinalari", "gaz-plitalari",
    "elektr-plitalar", "duxovkalar", "mikroto-lqinli-pechlar",
)
CHA, IDI, KON, ISI, VEN, HAV, TEL, SUV, OSH = (
    "changyutgichlar", "idish-yuvish-mashinalari", "konditsionerlar",
    "isitgichlar", "ventilyatorlar", "havo-tozalagichlar", "televizorlar",
    "suv-isitkichlar", "oshxona-texnikasi",
)

BRANDS: list[tuple[str, list[str]]] = [
    ("Artel", [MUZ, KIR, GAZ, ELP, DUX, MIK, CHA, KON, ISI, VEN, TEL, SUV, OSH]),
    ("Samsung", [MUZ, KIR, DUX, MIK, CHA, IDI, KON, HAV, TEL, OSH]),
    ("LG", [MUZ, KIR, DUX, MIK, CHA, IDI, KON, HAV, TEL, OSH]),
    ("Bosch", [MUZ, KIR, GAZ, ELP, DUX, MIK, CHA, IDI, SUV, OSH]),
    ("Beko", [MUZ, KIR, GAZ, ELP, DUX, IDI]),
    ("Indesit", [MUZ, KIR, GAZ]),
    ("Haier", [MUZ, KIR, IDI, KON]),
    ("Midea", [MUZ, KIR, GAZ, ELP, DUX, MIK, CHA, IDI, KON, ISI, VEN, HAV, SUV, OSH]),
    ("Shivaki", [MUZ, MIK, KON, ISI, VEN, TEL]),
    ("Premier", [MUZ, KIR, GAZ, MIK, KON, ISI, VEN, TEL, SUV, OSH]),
    ("Avalon", [MUZ, KIR, GAZ, MIK, CHA, KON, ISI, VEN, TEL, SUV, OSH]),
    ("Roison", [MUZ, KON, TEL]),
    ("Hofmann", [MUZ, KIR, KON]),
    ("Toshiba", [MUZ, MIK, TEL]),
    ("Hisense", [MUZ, KIR, KON, TEL]),
    ("Electrolux", [KIR, ELP, DUX, IDI, ISI, HAV, SUV]),
    ("Candy", [KIR]),
    ("Whirlpool", [KIR, IDI]),
    ("Gefest", [GAZ, ELP, DUX]),
    ("Ferre", [GAZ, ELP, DUX]),
    ("Hansa", [GAZ, ELP, DUX, IDI]),
    ("Greta", [GAZ]),
    ("Simfer", [GAZ, ELP, DUX]),
    ("Gorenje", [ELP, DUX, MIK, IDI, SUV]),
    ("Panasonic", [MIK, HAV, OSH]),
    ("Philips", [CHA, HAV, TEL, OSH]),
    ("Xiaomi", [CHA, ISI, VEN, HAV, TEL, OSH]),
    ("Karcher", [CHA]),
    ("Tefal", [CHA, OSH]),
    ("Dyson", [CHA, HAV]),
    ("Vitek", [CHA, ISI, VEN, OSH]),
    ("Scarlett", [CHA, ISI, VEN, OSH]),
    ("Gree", [KON]),
    ("Aux", [KON]),
    ("TCL", [KON, TEL]),
    ("Polaris", [ISI, VEN, SUV, OSH]),
    ("Ballu", [ISI, HAV]),
    ("Timberk", [ISI]),
    ("Bork", [VEN]),
    ("Sharp", [HAV]),
    ("Sony", [TEL]),
    ("Ariston", [SUV]),
    ("Thermex", [SUV]),
    ("Zanussi", [SUV]),
    ("Moulinex", [OSH]),
    ("Braun", [OSH]),
    ("Kenwood", [OSH]),
]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]


def upgrade() -> None:
    conn = op.get_bind()
    cat_id = {r[1]: str(r[0]) for r in conn.execute(sa.text("SELECT id, slug FROM catalog_categories")).fetchall()}

    insert_stmt = sa.text(
        "INSERT INTO catalog_brands (id, category_id, category_ids, slug, name, sort_order, is_active) "
        "VALUES (:id, :cid, CAST(:cids AS json), :slug, :name, :so, true) "
        "ON CONFLICT ON CONSTRAINT uq_brand_category_slug DO NOTHING"
    )
    for i, (name, cat_slugs) in enumerate(BRANDS):
        ids = [cat_id[s] for s in cat_slugs if s in cat_id]
        if not ids:
            continue
        slug = _slug(name)
        existing = conn.execute(
            sa.text("SELECT id, category_ids FROM catalog_brands WHERE slug = :s LIMIT 1"),
            {"s": slug},
        ).fetchone()
        if existing:
            cur = existing[1]
            if isinstance(cur, str):
                cur = json.loads(cur)
            merged = list(dict.fromkeys([*(cur or []), *ids]))   # union, keep order
            conn.execute(
                sa.text("UPDATE catalog_brands SET category_ids = CAST(:c AS json) WHERE id = :id"),
                {"c": json.dumps(merged), "id": existing[0]},
            )
        else:
            conn.execute(insert_stmt, {
                "id": uuid.uuid4(), "cid": ids[0], "cids": json.dumps(ids),
                "slug": slug, "name": name, "so": 1000 + i * 10,
            })


def downgrade() -> None:
    pass
