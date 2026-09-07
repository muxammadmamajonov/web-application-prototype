"""Seed brands for the Avto va mototexnika business type (Round 47)

Defined per category; the migration aggregates to one brand row per name
(internal repeats like Honda/Pioneer/Stels/Xiaomi collapse automatically) and
merges cross-segment namesakes by slug. Idempotent.

Revision ID: r47autobrands
Revises: r46furniturebrands
Create Date: 2026-05-31 21:00:00+00:00
"""
from __future__ import annotations

import json
import re
import uuid

import sqlalchemy as sa
from alembic import op

revision = "r47autobrands"
down_revision = "r46furniturebrands"
branch_labels = None
depends_on = None

# category slug → brand names (auto category slugs from r43)
CATEGORY_BRANDS: dict[str, list[str]] = {
    "avtomobillar": [
        "Chevrolet", "Ravon", "Daewoo", "Kia", "Hyundai", "Toyota", "Lexus", "Nissan",
        "Honda", "Mazda", "Mitsubishi", "Volkswagen", "Skoda", "Mercedes-Benz", "BMW",
        "Audi", "Tesla", "BYD", "Chery", "Haval", "Geely", "Changan", "Jetour", "Exeed",
        "Omoda", "Jaecoo", "Hongqi", "Li Auto", "Zeekr", "Nio", "Xpeng", "Leapmotor",
        "Deepal", "Avatr", "Tank", "GAC", "Aion", "Dongfeng", "Voyah", "FAW", "BAIC",
        "JAC", "MG", "Wuling",
    ],
    "mototsikllar": [
        "Honda", "Yamaha", "Suzuki", "Kawasaki", "BMW Motorrad", "Harley-Davidson",
        "Ducati", "KTM", "Bajaj", "TVS", "Lifan", "Zongshen", "Loncin", "CFMoto",
        "Shineray", "Voge", "Benelli", "QJMotor", "Benda", "Kayo", "Racer", "Motoland",
        "Irbis",
    ],
    "skuterlar": [
        "Honda", "Yamaha", "Suzuki", "Vespa", "Piaggio", "Aprilia", "Lifan", "Zongshen",
        "Loncin", "Voge", "CFMoto", "Kymco", "Sym", "Motoland", "Racer", "Irbis",
        "Stels", "Omaks", "Yadea", "Sunra", "Segway-Ninebot", "Kugoo", "Aima",
        "Luyuan", "Niu",
    ],
    "elektro-skuterlar": [
        "Yadea", "Sunra", "Aima", "Luyuan", "Niu", "Horwin", "Segway-Ninebot", "Xiaomi",
        "Kugoo", "Dualtron", "Minimotors", "Inokim", "Kaabo", "Joyor", "Hiper", "Citycoco",
    ],
    "velosipedlar": [
        "Giant", "Merida", "Trek", "Scott", "Cube", "Specialized", "Cannondale", "Stels",
        "Forward", "Phoenix", "Trinx", "Cronus", "Element", "Totem", "Xiaomi", "Himo",
        "ADO", "Engwe", "Fiido",
    ],
    "diskalar": [
        "K&K", "SKAD", "Vossen", "BBS", "Enkei", "Rays", "OZ Racing", "Borbet", "Replica",
        "Yamato", "Nitro", "Tech Line", "Mak", "LS Wheels", "Advanti", "Weds", "Rotiform",
    ],
    "shinalar": [
        "Michelin", "Bridgestone", "Continental", "Pirelli", "Goodyear", "Dunlop",
        "Yokohama", "Toyo", "Hankook", "Kumho", "Nexen", "Maxxis", "Sailun", "Triangle",
        "Linglong", "Goodride", "Westlake", "Roadstone", "Cordiant", "Kama", "Viatti",
        "Belshina", "Laufenn", "Marshal", "Nitto", "BFGoodrich", "Giti", "Kapsen",
    ],
    "akkumulyatorlar": [
        "Bosch", "Varta", "Mutlu", "Exide", "Energizer", "Tudor", "Rocket", "Delkor",
        "Hankook", "Solite", "Topla", "Bars", "Tyumen Battery", "Forse", "Akom",
        "Moratti", "Medalist", "Yuasa", "Panasonic", "Inci Aku", "AtlasBX",
    ],
    "avto-audio": [
        "Pioneer", "Sony", "Kenwood", "JBL", "Alpine", "JVC", "Hertz", "Morel", "Focal",
        "Kicker", "Rockford Fosgate", "Audison", "Nakamichi", "Bose", "Harman Kardon",
        "Infinity", "Blaupunkt", "Mystery", "Swat", "Ural",
    ],
    "avto-aksessuarlar": [
        "Hoco", "Baseus", "Xiaomi", "70mai", "DDPai", "BlackVue", "Garmin", "Pioneer",
        "Bosch", "Philips", "Osram", "Narva", "Momo", "Sparco", "Thule", "Yakima",
        "Turtle Wax", "Sonax", "Liqui Moly", "Castrol", "Shell", "Mobil", "Motul",
    ],
    "ehtiyot-qismlar": [
        "Bosch", "Denso", "NGK", "Delphi", "Valeo", "Mahle", "Mann Filter", "Filtron",
        "Sakura", "Febi Bilstein", "Lemforder", "Meyle", "Sachs", "Monroe", "KYB", "TRW",
        "Brembo", "Textar", "ATE", "Ferodo", "Gates", "Dayco", "SKF", "NTN", "Koyo",
        "GMB", "ZF", "Luk", "Exedy", "Aisin", "Hella", "Osram", "Philips",
        "GM Genuine Parts", "Toyota Genuine Parts", "Hyundai Mobis", "BYD Genuine Parts",
        "Chery Genuine Parts", "Geely Genuine Parts", "Changan Genuine Parts",
        "Haval Genuine Parts",
    ],
    "moy-va-texnik-suyuqliklar": [
        "Mobil", "Castrol", "Shell", "Motul", "Liqui Moly", "TotalEnergies", "Elf",
        "Valvoline", "ZIC", "Idemitsu", "Ravenol", "Mannol", "Lukoil", "Gazpromneft",
        "Rosneft", "Kixx", "Wolf", "Fuchs", "Addinol", "Repsol", "Bardahl", "Hi-Gear",
        "Felix", "Sintec",
    ],
}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]


def _aggregate() -> list[tuple[str, list[str]]]:
    """name → ordered list of category slugs (collapsing repeats)."""
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
                "slug": slug, "name": name, "so": 3000 + i * 10,
            })


def downgrade() -> None:
    pass
