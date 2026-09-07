"""Seed brands for the Uy uchun mahsulotlar business type (Round 51)

Aggregated per category. Unbranded is excluded here — it's the universal
"Brendsiz" brand (r50). Turkish letters are transliterated for clean slugs.

Revision ID: r51homegoodsbrands
Revises: r50constructbrands
Create Date: 2026-06-01 10:00:00+00:00
"""
from __future__ import annotations

import json
import re
import uuid

import sqlalchemy as sa
from alembic import op

revision = "r51homegoodsbrands"
down_revision = "r50constructbrands"
branch_labels = None
depends_on = None

CATEGORY_BRANDS: dict[str, list[str]] = {
    "gilamlar": ["SAG", "Merinos", "Dinarsu", "Pierre Cardin Home", "Royal Carpet",
                 "Atlantik", "Eko Halı", "Padişah Halı", "Kaşmir Halı", "Festival Halı",
                 "Artemis Halı", "Sanat Halı", "Brillant", "Bahariye", "Türkmen Halı",
                 "Bukhara Carpet", "Samarkand Carpet", "Imperia Carpet", "Local Manufacturer"],
    "pardalar": ["Tac", "Karaca Home", "English Home", "Madame Coco", "Pierre Cardin Home",
                 "Dinarsu", "IKEA", "Ozdilek", "Arya", "Tivolyo Home", "Cotton Box", "Sarev",
                 "Hobby Home", "Valeron", "First Choice", "Local Manufacturer"],
    "lyustralar": ["Philips", "Osram", "Eglo", "Maytoni", "Eurosvet", "Citilux", "Odeon Light",
                   "Favourite", "MW-Light", "Feron", "Gauss", "Navigator", "Jazzway",
                   "Camelion", "Novotech", "Elektrostandard", "Ambrella Light", "ST Luce",
                   "Loft IT", "Vitaluce", "Arlight", "Xiaomi", "Yeelight", "Paulmann",
                   "Lucide", "Arte Lamp", "Lightstar", "Lussole", "Artel", "Local Manufacturer"],
    "uy-dekorlari": ["IKEA", "Zara Home", "H&M Home", "English Home", "Karaca Home",
                     "Madame Coco", "Miniso", "Pierre Cardin Home", "Ozdilek", "Arya",
                     "Tivolyo Home", "Cotton Box", "Hobby Home", "Sarev", "Valeron",
                     "First Choice", "Local Manufacturer"],
    "oshxona-buyumlari": ["Tefal", "Karaca", "Korkmaz", "Fissman", "Berlinger Haus", "Kukmara",
                          "Luminarc", "Pasabahce", "Pyrex", "Vitesse", "Nadoba", "Rondell",
                          "Gipfel", "Wellberg", "Lessner", "Peterhof", "Bohmann", "Lara",
                          "Emsan", "Hisar", "Aryildiz", "Schafer", "IKEA", "Local Manufacturer"],
    "yotoqxona-buyumlari": ["Tac", "Karaca Home", "English Home", "Madame Coco", "IKEA",
                            "Dormeo", "Askona", "Ormatek", "Pierre Cardin Home", "Tivolyo Home",
                            "Cotton Box", "Sarev", "Hobby Home", "Valeron", "First Choice",
                            "Arya", "Ozdilek", "Local Manufacturer"],
    "hammom-jihozlari": ["Grohe", "Hansgrohe", "Roca", "Cersanit", "Vitra", "Geberit",
                         "Ideal Standard", "TECE", "IKEA", "Pasabahce", "Berossi", "Aquaphor",
                         "Barrier", "Local Manufacturer"],
    "ko-rpa-to-shaklar": ["Tac", "Karaca Home", "English Home", "Madame Coco", "Dormeo",
                          "Askona", "Ormatek", "IKEA", "Arya", "Ozdilek", "Tivolyo Home",
                          "Cotton Box", "Hobby Home", "Valeron", "First Choice", "Sarev",
                          "Local Manufacturer"],
    "uy-tekstili": ["Tac", "Karaca Home", "English Home", "Madame Coco", "Arya", "Ozdilek",
                    "Pierre Cardin Home", "Tivolyo Home", "Cotton Box", "Altinbasak",
                    "First Choice", "Hobby Home", "Issimo Home", "Sarev", "Valeron", "IKEA",
                    "Karven", "Kristal", "Local Manufacturer"],
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
                "slug": slug, "name": name, "so": 7000 + i * 10,
            })


def downgrade() -> None:
    pass
