"""Seed brands for the Quyosh energiyasi uskunalari business type (Round 52)

Note: the solar Akkumulyatorlar category slug is "akkumulyatorlar-solar"
(the auto segment took the base slug). Unbranded is excluded (universal Brendsiz).

Revision ID: r52solarbrands
Revises: r51homegoodsbrands
Create Date: 2026-06-01 11:00:00+00:00
"""
from __future__ import annotations

import json
import re
import uuid

import sqlalchemy as sa
from alembic import op

revision = "r52solarbrands"
down_revision = "r51homegoodsbrands"
branch_labels = None
depends_on = None

CATEGORY_BRANDS: dict[str, list[str]] = {
    "quyosh-panellari": ["Jinko Solar", "Longi Solar", "JA Solar", "Trina Solar",
        "Canadian Solar", "Risen Energy", "Astronergy", "TW Solar", "Seraphim", "Suntech",
        "Qcells", "REC", "AE Solar", "Talesun", "HT-SAAE", "Phono Solar", "Znshine Solar",
        "GCL Solar", "Leapton Solar", "Luxen Solar", "Vikram Solar", "Hanwha Qcells",
        "Hyundai Energy", "Sharp Solar", "SunPower", "Yingli Solar", "Solarland",
        "Local Supplier"],
    "invertorlar": ["Huawei", "Growatt", "Deye", "GoodWe", "Sungrow", "Solis", "SMA",
        "Fronius", "Victron Energy", "LuxPower", "Must", "Voltronic Power", "Inverex",
        "Sofar Solar", "SRNE", "PowMr", "TBB Power", "Delta", "Schneider Electric", "ABB",
        "Hoymiles", "APsystems", "FoxESS", "SAKO", "Easun Power", "Felicity Solar",
        "Local Supplier"],
    "akkumulyatorlar-solar": ["Felicity Solar", "Dyness", "Pylontech", "BYD Battery",
        "Huawei Luna", "Growatt ARK", "Deye Battery", "Must Battery", "SRNE", "Narada",
        "Sacred Sun", "Vision Battery", "Leoch", "Ritar", "Eastman", "Exide", "Rocket",
        "Delkor", "Varta", "Bosch", "Yuasa", "Trojan", "CATL", "EVE Energy",
        "Felicity Lithium", "Local Supplier"],
    "solar-lampalar": ["Philips", "Osram", "Feron", "Gauss", "Camelion", "Navigator",
        "Jazzway", "Xiaomi", "Yeelight", "SolarX", "Sunlight", "Greenlight", "LEDVANCE",
        "DELIXI", "Local Supplier"],
    "solar-komplektlar": ["Jinko Solar", "Longi Solar", "JA Solar", "Trina Solar", "Huawei",
        "Growatt", "Deye", "GoodWe", "Sungrow", "Must", "Felicity Solar", "Victron Energy",
        "LuxPower", "Solis", "SRNE", "Pylontech", "Dyness", "Local Supplier"],
    "zaryad-stansiyalari": ["EcoFlow", "Bluetti", "Jackery", "Anker Solix", "DJI Power",
        "Allpowers", "Oukitel", "Ugreen", "Growatt", "Deye", "Victron Energy", "Huawei",
        "Delta", "Zendure", "Pecron", "Fossibot", "FlashFish", "Local Supplier"],
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
                "slug": slug, "name": name, "so": 8000 + i * 10,
            })


def downgrade() -> None:
    pass
