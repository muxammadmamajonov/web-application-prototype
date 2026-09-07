"""Seed brands for the Bolalar mahsulotlari business type (Round 54)

Kids "Velosipedlar" and "Bolalar mebellari" use the de-duplicated slugs
("velosipedlar-kids", "bolalar-mebellari-kids") since auto/furniture took the
base slugs — so bike/furniture brands merge across those segments. Unbranded
excluded (universal Brendsiz); Turkish letters transliterated.

Revision ID: r54kidsbrands
Revises: r53securitybrands
Create Date: 2026-06-01 13:00:00+00:00
"""
from __future__ import annotations

import json
import re
import uuid

import sqlalchemy as sa
from alembic import op

revision = "r54kidsbrands"
down_revision = "r53securitybrands"
branch_labels = None
depends_on = None

CATEGORY_BRANDS: dict[str, list[str]] = {
    "aravachalar": ["Chicco", "Kinderkraft", "Babyzen", "Cybex", "Joie", "Peg Perego",
        "Inglesina", "Graco", "Evenflo", "Happy Baby", "Rant", "Carrello", "Anex", "Tutis",
        "Bebetto", "Adamex", "ABC Design", "Britax Römer", "Local Supplier"],
    "velosipedlar-kids": ["Stels", "Forward", "Giant", "Merida", "Trek", "Scott", "Cube",
        "Phoenix", "Trinx", "Cronus", "Totem", "Royal Baby", "Novatrack", "Pilot",
        "Local Supplier"],
    "elektromobillar": ["Mercedes-Benz Kids", "BMW Kids", "Audi Kids", "Lamborghini Kids",
        "Toyota Kids", "Range Rover Kids", "Ford Kids", "Bentley Kids", "Jeep Kids",
        "Bugatti Kids", "Barty", "RiverToys", "Chi Lok Bo", "Local Supplier"],
    "karovatlar": ["IKEA", "Chicco", "Happy Baby", "Babyroom", "Giovanni", "Micuna",
        "Geuther", "Feretti", "Local Manufacturer"],
    "o-yinchoqlar": ["LEGO", "Mattel", "Hot Wheels", "Barbie", "Fisher-Price", "Hasbro",
        "Nerf", "Play-Doh", "Disney", "Marvel", "DC", "Bruder", "Little Tikes",
        "Melissa & Doug", "Clementoni", "Baby Born", "LOL Surprise", "Local Supplier"],
    "maktab-buyumlari": ["ErichKrause", "Berlingo", "Hatber", "Brauberg", "Parker", "Pilot",
        "Schneider", "Faber-Castell", "Maped", "Staedtler", "Stabilo", "Bic", "Deli",
        "Artel Stationery", "Local Supplier"],
    "bolalar-mebellari-kids": ["IKEA", "Bellona", "Istikbal", "Babyroom", "Kids Furniture",
        "Giovanni", "Micuna", "Geuther", "Local Manufacturer"],
    "chaqaloq-mahsulotlari": ["Chicco", "Pigeon", "Avent", "Philips Avent", "Canpol Babies",
        "Dr. Brown's", "Nuk", "Tommee Tippee", "Babyono", "Happy Baby", "Mothercare",
        "Munchkin", "Medela", "Local Supplier"],
}

_TR = str.maketrans({"ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ç": "c", "Ç": "c",
                     "ğ": "g", "Ğ": "g", "ü": "u", "Ü": "u", "ö": "o", "Ö": "o"})


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.translate(_TR).lower()).strip("-")[:40]


def _aggregate() -> list[tuple[str, list[str]]]:
    out: dict[str, list[str]] = {}
    order: list[str] = []
    for cat, names in CATEGORY_BRANDS.items():
        for n in names:
            if n not in out:
                out[n] = []
                order.append(n)
            if cat not in out[n]:
                out[n].append(cat)
    return [(n, out[n]) for n in order]


def upgrade() -> None:
    conn = op.get_bind()
    cat_id = {r[1]: str(r[0]) for r in conn.execute(sa.text("SELECT id, slug FROM catalog_categories")).fetchall()}

    insert_stmt = sa.text(
        "INSERT INTO catalog_brands (id, category_id, category_ids, slug, name, sort_order, is_active) "
        "VALUES (:id, :cid, CAST(:cids AS json), :slug, :name, :so, true) "
        "ON CONFLICT ON CONSTRAINT uq_brand_category_slug DO NOTHING"
    )
    for i, (name, cat_slugs) in enumerate(_aggregate()):
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
            merged = list(dict.fromkeys([*(cur or []), *ids]))
            conn.execute(
                sa.text("UPDATE catalog_brands SET category_ids = CAST(:c AS json) WHERE id = :id"),
                {"c": json.dumps(merged), "id": existing[0]},
            )
        else:
            conn.execute(insert_stmt, {
                "id": uuid.uuid4(), "cid": ids[0], "cids": json.dumps(ids),
                "slug": slug, "name": name, "so": 10000 + i * 10,
            })


def downgrade() -> None:
    pass
