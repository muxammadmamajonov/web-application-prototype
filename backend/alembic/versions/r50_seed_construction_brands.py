"""Seed Qurilish brands + universal "Brendsiz" (no-brand) option (Round 50)

Construction brands are aggregated per category like the other segments.
"Unbranded" is turned into a single universal brand named **Brendsiz**
("no brand" / other) linked to EVERY category and sorted last, so any product
can be added without a real brand. This also renames the jewelry "Unbranded".

Revision ID: r50constructbrands
Revises: r49jewelrybrands
Create Date: 2026-06-01 09:00:00+00:00
"""
from __future__ import annotations

import json
import re
import uuid

import sqlalchemy as sa
from alembic import op

revision = "r50constructbrands"
down_revision = "r49jewelrybrands"
branch_labels = None
depends_on = None

# Per category (Unbranded handled separately as the universal "Brendsiz").
CATEGORY_BRANDS: dict[str, list[str]] = {
    "sement": ["Akhangarancement", "Qizilqumsement", "Bekabadsement", "Kuvasaysement",
               "Ohangaron Cement", "Eurocement", "Lafarge", "Heidelberg Materials"],
    "g-isht": ["AKFA Build", "Golden Bricks", "Mega Block", "Eco Block", "Brick House",
               "Local Manufacturer"],
    "armatura": ["Uzmetkombinat", "Bekabad Steel", "Metall Invest", "Art Metal",
                 "Grand Metal", "Local Manufacturer"],
    "kafel": ["Kerama Marazzi", "Cersanit", "Kerranova", "Unitile", "Grasaro", "Estima",
              "Laparet", "AKFA Build", "Local Manufacturer"],
    "laminat": ["Kronospan", "Egger", "Classen", "Tarkett", "Swiss Krono", "Kastamonu",
                "Kronotex", "Local Manufacturer"],
    "linoleum": ["Tarkett", "Juteks", "IVC", "Ideal", "Beauflor", "Local Manufacturer"],
    "bo-yoqlar": ["Tikkurila", "Dulux", "Marshall", "Alpina", "Caparol", "Dyo", "Eskaro",
                  "Teknos", "Local Manufacturer"],
    "santexnika": ["AKFA", "Grohe", "Hansgrohe", "Roca", "Cersanit", "Vitra",
                   "Ideal Standard", "Geberit", "TECE", "Local Manufacturer"],
    "elektr-jihozlari": ["Schneider Electric", "Legrand", "ABB", "IEK", "EKF", "Hager",
                         "Chint", "Panasonic", "Philips", "Local Manufacturer"],
    "eshiklar": ["AKFA", "MDF Lux", "DoorHan", "Bravo Doors", "Profil Doors",
                 "Local Manufacturer"],
    "derazalar": ["AKFA", "Engelberg", "Imzo", "Rehau", "VEKA", "KBE", "Local Manufacturer"],
    "tom-yopish-materiallari": ["Ruukki", "Grand Line", "Metall Profil", "Tegola",
                                "Katepal", "Shinglas", "Local Manufacturer"],
    "qurilish-asboblari": ["Bosch", "Makita", "DeWalt", "Milwaukee", "Stanley", "Total",
                           "Ingco", "Ronix", "Metabo", "Hikoki", "Crown", "Tolsen"],
}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]


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
    rows = conn.execute(sa.text("SELECT id, slug FROM catalog_categories")).fetchall()
    cat_id = {r[1]: str(r[0]) for r in rows}
    all_ids = [str(r[0]) for r in rows]

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
                "slug": slug, "name": name, "so": 6000 + i * 10,
            })

    # Universal "Brendsiz" (no brand) — linked to every category, shown last.
    if all_ids:
        unb = conn.execute(sa.text("SELECT id FROM catalog_brands WHERE slug = 'unbranded' LIMIT 1")).fetchone()
        if unb:
            conn.execute(
                sa.text("UPDATE catalog_brands SET name = 'Brendsiz', category_id = :cid, "
                        "category_ids = CAST(:c AS json), sort_order = 99999 WHERE id = :id"),
                {"cid": all_ids[0], "c": json.dumps(all_ids), "id": unb[0]},
            )
        else:
            conn.execute(insert_stmt, {
                "id": uuid.uuid4(), "cid": all_ids[0], "cids": json.dumps(all_ids),
                "slug": "unbranded", "name": "Brendsiz", "so": 99999,
            })


def downgrade() -> None:
    pass
