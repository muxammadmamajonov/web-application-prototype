"""Seed brands for the Kiyim va oyoq kiyim business type (Round 48)

Per-category lists aggregated to one brand row per name (Nike/Zara/Tommy
Hilfiger collapse across categories). Kids sub-lines (e.g. "Zara Kids") are kept
as their own brands as listed. Idempotent merge-by-slug.

Revision ID: r48clothingbrands
Revises: r47autobrands
Create Date: 2026-05-31 22:00:00+00:00
"""
from __future__ import annotations

import json
import re
import uuid

import sqlalchemy as sa
from alembic import op

revision = "r48clothingbrands"
down_revision = "r47autobrands"
branch_labels = None
depends_on = None

CATEGORY_BRANDS: dict[str, list[str]] = {
    "erkaklar-kiyimi": [
        "Zara", "LC Waikiki", "DeFacto", "Koton", "Colin's", "Mavi", "Levi's", "Wrangler",
        "Lee", "Diesel", "Tommy Hilfiger", "Calvin Klein", "Lacoste", "Polo Ralph Lauren",
        "Hugo Boss", "Armani Exchange", "Emporio Armani", "Pierre Cardin", "US Polo Assn.",
        "Massimo Dutti", "Mango Man", "Pull&Bear", "Bershka", "H&M", "Uniqlo", "GAP",
        "Next", "O'Stin", "Gloria Jeans", "Reserved", "Kigili", "Ramsey", "Network",
    ],
    "ayollar-kiyimi": [
        "Zara", "Mango", "H&M", "LC Waikiki", "DeFacto", "Koton", "Bershka", "Stradivarius",
        "Pull&Bear", "Massimo Dutti", "Reserved", "Mohito", "Sinsay", "OYSHO", "Guess",
        "Calvin Klein", "Tommy Hilfiger", "Armani Exchange", "Michael Kors", "Trendyol",
        "Next", "Marks & Spencer", "Dorothy Perkins", "New Look",
    ],
    "bolalar-kiyimi": [
        "LC Waikiki Kids", "DeFacto Kids", "Zara Kids", "H&M Kids", "Koton Kids",
        "Next Kids", "Mothercare", "Chicco", "Carter's", "Waikiki Baby", "Baby Go",
        "Mini Boss", "Disney Kids",
    ],
    "erkaklar-oyoq-kiyimi": [
        "Nike", "Adidas", "Puma", "Reebok", "New Balance", "Skechers", "ECCO", "Geox",
        "Clarks", "Timberland", "Lacoste", "Tommy Hilfiger", "US Polo Assn.", "CAT",
        "Columbia", "Converse", "Vans",
    ],
    "ayollar-oyoq-kiyimi": [
        "Nike", "Adidas", "Puma", "Skechers", "ECCO", "Geox", "Aldo", "Tamaris",
        "Carlo Pazolini", "Zara", "Mango", "Bershka", "Stradivarius", "Guess",
        "Michael Kors", "Steve Madden",
    ],
    "sport-kiyimlari": [
        "Nike", "Adidas", "Puma", "Reebok", "New Balance", "Under Armour", "Asics",
        "Mizuno", "Umbro", "Joma", "Kappa", "Fila", "Columbia", "The North Face",
        "Decathlon",
    ],
    "sumkalar": [
        "Michael Kors", "Guess", "Coach", "Furla", "Zara", "Mango", "Tommy Hilfiger",
        "Calvin Klein", "Pierre Cardin", "Samsonite", "American Tourister", "Tumi",
        "Aldo", "Charles & Keith",
    ],
    "soatlar": [
        "Casio", "G-Shock", "Seiko", "Citizen", "Orient", "Tissot", "Longines", "Rado",
        "Rolex", "Omega", "Fossil", "Emporio Armani", "Tommy Hilfiger",
        "Daniel Wellington", "Michael Kors",
    ],
    "aksessuarlar": [
        "Nike", "Adidas", "Puma", "Zara", "H&M", "Mango", "Tommy Hilfiger", "Calvin Klein",
        "Guess", "Lacoste", "Pierre Cardin", "Levi's", "US Polo Assn.",
    ],
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
                "slug": slug, "name": name, "so": 4000 + i * 10,
            })


def downgrade() -> None:
    pass
