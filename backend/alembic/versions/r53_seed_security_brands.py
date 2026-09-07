"""Seed brands for the Xavfsizlik tizimlari business type (Round 53)

Fixes while transcribing the source list:
* "Akuvoox" (a typo duplicate of "Akuvox") is dropped.
* "Uniview (UNV)" normalised to "Uniview" so it's one brand across categories.
Unbranded is excluded (universal Brendsiz).

Revision ID: r53securitybrands
Revises: r52solarbrands
Create Date: 2026-06-01 12:00:00+00:00
"""
from __future__ import annotations

import json
import re
import uuid

import sqlalchemy as sa
from alembic import op

revision = "r53securitybrands"
down_revision = "r52solarbrands"
branch_labels = None
depends_on = None

CATEGORY_BRANDS: dict[str, list[str]] = {
    "videokameralar": ["Hikvision", "Dahua", "EZVIZ", "IMOU", "Uniview", "Tiandy",
        "TP-Link VIGI", "Axis", "Bosch Security", "Hanwha Vision", "Hikmicro",
        "Provision ISR", "TVT", "Jovision", "ZKTeco", "Xiaomi", "Aqara", "Reolink",
        "Arlo", "Eufy", "Tapo", "Hiklook", "HiWatch", "Longse", "Milesight", "VStarcam",
        "Foscam", "Local Supplier"],
    "dvr-nvr-qurilmalar": ["Hikvision", "Dahua", "Uniview", "Tiandy", "TVT", "Provision ISR",
        "Hiklook", "HiWatch", "Milesight", "ZKTeco", "Bosch Security", "Hanwha Vision",
        "Axis", "Local Supplier"],
    "domofonlar": ["Hikvision", "Dahua", "Akuvox", "Commax", "Kocom", "Slinex", "Bas-IP",
        "Fanvil", "ZKTeco", "Bticino", "Fermax", "Aiphone", "Hiklook", "Local Supplier"],
    "signalizatsiyalar": ["Ajax", "Hikvision", "Dahua", "Satel", "Paradox", "DSC", "Texecom",
        "Bosch Security", "Jablotron", "Pyronix", "Crow", "Risco", "ZKTeco", "Local Supplier"],
    "smart-qulf-tizimlari": ["Aqara", "Xiaomi", "Philips Smart Lock", "Kaadas",
        "Samsung Smart Door Lock", "Yale", "TTLock", "Tuya Smart", "ZKTeco", "Hikvision",
        "Dahua", "Solity", "Igloohome", "August", "Nuki", "Local Supplier"],
    "kirishni-nazorat-qilish-tizimlari": ["ZKTeco", "Hikvision", "Dahua", "Suprema", "Anviz",
        "Rosslare", "HID Global", "Honeywell", "Bosch Security", "Suprema BioStar",
        "HikCentral", "Dahua DSS", "Matrix", "ESSL", "Local Supplier"],
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
                "slug": slug, "name": name, "so": 9000 + i * 10,
            })


def downgrade() -> None:
    pass
