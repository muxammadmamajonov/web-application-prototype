"""Seed brands for the Zargarlik buyumlari business type (Round 49)

Revision ID: r49jewelrybrands
Revises: r48clothingbrands
Create Date: 2026-05-31 23:00:00+00:00
"""
from __future__ import annotations

import json
import re
import uuid

import sqlalchemy as sa
from alembic import op

revision = "r49jewelrybrands"
down_revision = "r48clothingbrands"
branch_labels = None
depends_on = None

CATEGORY_BRANDS: dict[str, list[str]] = {
    "uzuklar": ["Unbranded", "Pandora", "Swarovski", "Cartier", "Tiffany & Co.", "Bvlgari",
                "TOUS", "SOKOLOV", "Sunlight", "Damiani", "Chopard", "Harry Winston", "Graff"],
    "ziraklar": ["Unbranded", "Pandora", "Swarovski", "Cartier", "Tiffany & Co.", "Bvlgari",
                 "TOUS", "SOKOLOV", "Sunlight", "Damiani", "Chopard", "Graff"],
    "brasletlar": ["Unbranded", "Pandora", "Swarovski", "Cartier", "Tiffany & Co.", "Bvlgari",
                   "TOUS", "SOKOLOV", "Sunlight", "Damiani"],
    "zanjirlar": ["Unbranded", "Pandora", "Tiffany & Co.", "Cartier", "Bvlgari", "SOKOLOV",
                  "Sunlight", "TOUS"],
    "kulonlar": ["Unbranded", "Pandora", "Swarovski", "Tiffany & Co.", "Cartier", "TOUS", "SOKOLOV"],
    "komplektlar": ["Unbranded", "Pandora", "Swarovski", "Cartier", "Tiffany & Co.", "Bvlgari",
                    "TOUS", "SOKOLOV"],
    "erkaklar-zargarligi": ["Unbranded", "Cartier", "Bvlgari", "Tiffany & Co.", "Chopard",
                            "SOKOLOV", "Police Jewelry", "Thomas Sabo"],
    "kumush-buyumlar": ["Unbranded", "Pandora", "Swarovski", "TOUS", "SOKOLOV", "Sunlight",
                        "Thomas Sabo", "Nomination"],
    "tilla-buyumlar": ["Unbranded", "Cartier", "Tiffany & Co.", "Bvlgari", "Chopard", "Damiani",
                       "Graff", "Harry Winston", "Van Cleef & Arpels"],
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
                "slug": slug, "name": name, "so": 5000 + i * 10,
            })


def downgrade() -> None:
    pass
