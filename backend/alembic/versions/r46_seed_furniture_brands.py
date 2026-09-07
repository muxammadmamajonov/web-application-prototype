"""Seed brands for the Mebel (furniture) business type (Round 46)

One brand row per name, linked to every furniture category it appears in.
Merge-by-slug (idempotent); furniture brands don't overlap other segments here.

Revision ID: r46furniturebrands
Revises: r45applianbrands
Create Date: 2026-05-31 20:00:00+00:00
"""
from __future__ import annotations

import json
import re
import uuid

import sqlalchemy as sa
from alembic import op

revision = "r46furniturebrands"
down_revision = "r45applianbrands"
branch_labels = None
depends_on = None

DIV, KRO, SHK, KOM, STO = (
    "divanlar", "krovatlar", "shkaflar", "komodlar", "stol-va-stullar",
)
OSHM, YUM, OFI, BOL, MAT, MTP = (
    "oshxona-mebellari", "yumshoq-mebellar", "ofis-mebellari",
    "bolalar-mebellari", "matraslar", "mebel-to-plamlari",
)

BRANDS: list[tuple[str, list[str]]] = [
    ("Bellona", [DIV, KRO, SHK, KOM, STO, OSHM, YUM, BOL, MAT, MTP]),
    ("Istikbal", [DIV, KRO, SHK, KOM, STO, OSHM, YUM, BOL, MAT, MTP]),
    ("Alfemo", [DIV, KRO, SHK, KOM, STO, OSHM, YUM, MTP]),
    ("Mondi", [DIV, KRO, SHK, KOM, STO, OSHM, YUM, MTP]),
    ("Evdebiz", [DIV, YUM]),
    ("Mebel House", [DIV, KRO, SHK, KOM, STO, OSHM, YUM, OFI, BOL, MTP]),
    ("Asr Mebel", [DIV, KRO, SHK, KOM, STO, OSHM, YUM, OFI, BOL, MTP]),
    ("Ideal Mebel", [DIV, KRO, SHK, KOM, STO, OSHM, YUM, OFI, BOL, MTP]),
    ("Dream House", [DIV, KRO, SHK, MTP]),
    ("Comfort Mebel", [DIV, KRO, SHK, KOM, YUM, MTP]),
    ("Archa Mebel", [SHK, OSHM]),
    ("IKEA", [STO, OFI, BOL]),
    ("Signal", [STO]),
    ("Halmar", [STO]),
    ("Verona Kitchen", [OSHM]),
    ("MDF Lux", [OSHM]),
    ("Ergolife", [OFI]),
    ("Steelcase", [OFI]),
    ("Herman Miller", [OFI]),
    ("Art Office", [OFI]),
    ("Babyroom", [BOL]),
    ("Kids Furniture", [BOL]),
    ("Askona", [MAT]),
    ("Ormatek", [MAT]),
    ("Dormeo", [MAT]),
    ("Sonberry", [MAT]),
    ("Sleep House", [MAT]),
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
            merged = list(dict.fromkeys([*(cur or []), *ids]))
            conn.execute(
                sa.text("UPDATE catalog_brands SET category_ids = CAST(:c AS json) WHERE id = :id"),
                {"c": json.dumps(merged), "id": existing[0]},
            )
        else:
            conn.execute(insert_stmt, {
                "id": uuid.uuid4(), "cid": ids[0], "cids": json.dumps(ids),
                "slug": slug, "name": name, "so": 2000 + i * 10,
            })


def downgrade() -> None:
    pass
