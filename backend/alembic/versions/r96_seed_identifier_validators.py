"""Give every catalog category a real, validated identifier_schema (MUDD-113 A)

r55 seeded ``identifier_schema`` for most categories, but every field was
optional and carried nothing beyond a ``max_len`` — no field was required, and
nothing declared *how* to validate it (a "VIN" was just a 20-char text box, no
different from a shop's own note). Owner requirement for this pass: every
category needs at least one **required** identifier and at least one
**optional** one, and every field carries its own min/max bound plus a
``validator`` key that ``app/core/catalog_identifiers.py`` actually enforces.

Field shape (superset of r55's): ``{key, label_uz, label_ru, type, required,
min_len?, max_len?, validator?}``. ``validator`` is one of:
  * ``imei``      — 15 digits, GSMA Luhn check digit (phones/feature phones).
  * ``vin``       — 17 chars, ISO 3779 charset (no I/O/Q) (cars/motos/scooters).
  * ``mac``       — colon/hyphen-separated MAC address (routers, IP cameras).
  * ``inventory`` — shop-assigned tag, alnum + separators (serialless goods).
  * ``plate``     — vehicle registration plate, loose charset (not pinned to
    one grammar — plate formats vary by issue year even within Uzbekistan).
  * ``serial``    — manufacturer serial / model code: letters+digits+separators
    (reuses the same normalizer as ``app/core/identifiers.py``'s SERIAL_OR_CONTRACT).

Coverage decisions:
  * Phones (`smartfonlar`, `tugmali-telefonlar`): IMEI required (a device's
    real identity), second IMEI + serial optional (dual-SIM / not always
    printed accessibly).
  * Tablets/laptops/computers/monitors/printers/wearables/appliances/TVs/
    power tools/solar gear/security gear: manufacturer serial required
    (printed on every unit), model code optional (distinguishes SKUs a bare
    serial doesn't, e.g. regional variants).
  * Cars/motorcycles/scooters: VIN required (the one number that survives
    resale and re-registration), plate + engine number optional — a vehicle
    can be sold before re-registration, so the plate is not guaranteed to be
    final at sale time.
  * Routers/IP cameras: serial required, MAC optional (useful for network
    installs, not printed on every budget unit).
  * Categories with **no natural per-unit serial** (furniture, clothing,
    jewelry, home goods, construction materials, kids' soft goods, bicycles'
    consumable parts, and the generic "other" bucket): inventing a fake
    manufacturer ID would be dishonest, so the required field is the shop's
    *own* inventory tag instead (what the store already needs to track stock),
    with a manufacturer batch/article number as the optional second field.

Idempotent: overwrites each category's ``identifier_schema`` by slug, same
pattern as r55. Does not touch ``attribute_schema``.

⚠ DOWNGRADE IS NOT A RESTORE (MUDD-123/L2)
------------------------------------------
``downgrade()`` sets all 114 categories' ``identifier_schema`` to ``'[]'`` — it
does **not** put r55's field definitions back. Rolling this deploy back
therefore leaves the catalog in a state neither release ever shipped: every
category loses its identifier fields entirely, so the sale form stops offering
IMEI/VIN/serial boxes at all, and the labels a tenant sees vanish rather than
revert. Nothing is lost that cannot be re-seeded (re-running ``upgrade()``
restores this file's definitions verbatim), but a rollback is a **forward fix**
here, not a time machine. Existing ``installment_item_identifiers`` rows are
untouched by either direction.

⚠ THIS MIGRATION HAS A RETROACTIVE COST (MUDD-123/M4)
-----------------------------------------------------
Making a field ``required`` changes what the API demands of data that already
exists. `PUT /installments/{iid}` deletes and recreates every line, so it
re-validates old rows under these new rules, and there is no backfill —
`rk0catident` created ``installment_item_identifiers`` empty. Left alone, that
made every pre-existing contract in these categories permanently un-editable.
The endpoint now grandfathers a replaced line's never-captured and unchanged
values (see ``_validate_item_identifiers`` in
``app/api/tenant/installments.py`` and ``docs/CATALOG_IDENTIFIERS.md``); a
future migration that tightens a schema the same way must assume the same
obligation.

Revision ID: r96catidschema
Revises: rk0catident
Create Date: 2026-08-29 13:30:00+00:00
"""
from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

revision = "r96catidschema"
down_revision = "rk0catident"
branch_labels = None
depends_on = None


def F(key, uz, ru, *, required=False, validator=None, min_len=None, max_len=None):
    d = {"key": key, "label_uz": uz, "label_ru": ru, "type": "text", "required": required}
    if validator:
        d["validator"] = validator
    if min_len:
        d["min_len"] = min_len
    if max_len:
        d["max_len"] = max_len
    return d


def imei(key, uz, ru, *, required):
    return F(key, uz, ru, required=required, validator="imei", min_len=15, max_len=15)


def serial(*, required, key="serial", uz="Seriya raqami", ru="Серийный номер"):
    return F(key, uz, ru, required=required, validator="serial", min_len=4, max_len=40)


IMEI1_REQ = imei("imei1", "IMEI 1", "IMEI 1", required=True)
IMEI2_OPT = imei("imei2", "IMEI 2", "IMEI 2", required=False)
IMEI1_OPT = imei("imei1", "IMEI 1", "IMEI 1", required=False)

SERIAL_REQ = serial(required=True)
SERIAL_OPT = serial(required=False)

MODEL_CODE_OPT = F("model_code", "Model kodi", "Код модели",
                    required=False, validator="serial", min_len=2, max_len=40)

VIN_REQ = F("vin", "Kuzov / VIN raqami", "Кузов / VIN",
            required=True, validator="vin", min_len=17, max_len=17)
PLATE_OPT = F("plate", "Davlat raqami", "Гос. номер",
              required=False, validator="plate", min_len=6, max_len=12)
ENGINE_OPT = F("engine", "Dvigatel raqami", "Номер двигателя",
               required=False, validator="serial", min_len=4, max_len=20)

MAC_OPT = F("mac", "MAC manzil", "MAC-адрес",
            required=False, validator="mac", min_len=17, max_len=17)

INVENTORY_REQ = F("inventory_no", "Do'kon inventar raqami", "Инвентарный номер магазина",
                   required=True, validator="inventory", min_len=3, max_len=24)
BATCH_OPT = F("batch_no", "Partiya / artikul raqami", "Номер партии / артикул",
              required=False, validator="serial", min_len=1, max_len=40)

# ── profiles ──────────────────────────────────────────────────────────────
PHONE = [IMEI1_REQ, IMEI2_OPT, SERIAL_OPT]
FEATURE_PHONE = [IMEI1_REQ, IMEI2_OPT]
TABLET = [SERIAL_REQ, IMEI1_OPT]
SERIAL_MODEL = [SERIAL_REQ, MODEL_CODE_OPT]
ROUTER_OR_CAMERA = [SERIAL_REQ, MAC_OPT]
VEHICLE = [VIN_REQ, PLATE_OPT, ENGINE_OPT]
INVENTORY_TAG = [INVENTORY_REQ, BATCH_OPT]

SERIAL_MODEL_SLUGS = [
    # electronics
    "noutbuklar", "kompyuterlar", "monitorlar", "printerlar", "smart-soatlar",
    "quloqchinlar", "kolonkalar", "o-yin-konsollari",
    # appliances
    "muzlatgichlar", "kir-yuvish-mashinalari", "gaz-plitalari", "elektr-plitalar",
    "duxovkalar", "mikroto-lqinli-pechlar", "changyutgichlar",
    "idish-yuvish-mashinalari", "konditsionerlar", "isitgichlar", "ventilyatorlar",
    "havo-tozalagichlar", "televizorlar", "suv-isitkichlar", "oshxona-texnikasi",
    # auto (non-vehicle, serialised units)
    "elektro-skuterlar", "velosipedlar", "avto-audio",
    # clothing (watches carry a real serial)
    "soatlar",
    # construction (power tools)
    "qurilish-asboblari",
    # solar
    "quyosh-panellari", "invertorlar", "akkumulyatorlar-solar",
    "solar-komplektlar", "zaryad-stansiyalari",
    # security
    "dvr-nvr-qurilmalar", "domofonlar", "signalizatsiyalar",
    "smart-qulf-tizimlari", "kirishni-nazorat-qilish-tizimlari",
]

INVENTORY_TAG_SLUGS = [
    # electronics accessories with no per-unit serial
    "powerbanklar", "telefon-aksessuarlari", "kompyuter-aksessuarlari",
    # furniture
    "divanlar", "krovatlar", "shkaflar", "komodlar", "stol-va-stullar",
    "oshxona-mebellari", "yumshoq-mebellar", "ofis-mebellari",
    "bolalar-mebellari", "matraslar", "mebel-to-plamlari",
    # auto parts/consumables
    "diskalar", "shinalar", "akkumulyatorlar", "avto-aksessuarlar",
    "ehtiyot-qismlar", "moy-va-texnik-suyuqliklar",
    # clothing
    "erkaklar-kiyimi", "ayollar-kiyimi", "bolalar-kiyimi",
    "erkaklar-oyoq-kiyimi", "ayollar-oyoq-kiyimi", "sport-kiyimlari",
    "sumkalar", "aksessuarlar",
    # jewelry
    "uzuklar", "ziraklar", "brasletlar", "zanjirlar", "kulonlar",
    "komplektlar", "erkaklar-zargarligi", "kumush-buyumlar", "tilla-buyumlar",
    # construction materials
    "sement", "g-isht", "armatura", "kafel", "laminat", "linoleum",
    "bo-yoqlar", "santexnika", "elektr-jihozlari", "eshiklar", "derazalar",
    "tom-yopish-materiallari",
    # home goods
    "gilamlar", "pardalar", "lyustralar", "uy-dekorlari", "oshxona-buyumlari",
    "yotoqxona-buyumlari", "hammom-jihozlari", "ko-rpa-to-shaklar", "uy-tekstili",
    # solar (non-serialised)
    "solar-lampalar",
    # kids
    "aravachalar", "velosipedlar-kids", "elektromobillar", "karovatlar",
    "o-yinchoqlar", "maktab-buyumlari", "bolalar-mebellari-kids",
    "chaqaloq-mahsulotlari",
    # other
    "boshqa-mahsulotlar",
]

IDENTIFIERS: dict[str, list] = {
    "smartfonlar": PHONE,
    "tugmali-telefonlar": FEATURE_PHONE,
    "planshetlar": TABLET,
    "router-va-modemlar": ROUTER_OR_CAMERA,
    "videokameralar": ROUTER_OR_CAMERA,
    "avtomobillar": VEHICLE,
    "mototsikllar": VEHICLE,
    "skuterlar": VEHICLE,
    **{slug: SERIAL_MODEL for slug in SERIAL_MODEL_SLUGS},
    **{slug: INVENTORY_TAG for slug in INVENTORY_TAG_SLUGS},
}


def upgrade() -> None:
    conn = op.get_bind()
    stmt = sa.text(
        "UPDATE catalog_categories SET identifier_schema = CAST(:i AS json) WHERE slug = :slug"
    )
    for slug, fields in IDENTIFIERS.items():
        result = conn.execute(stmt, {"slug": slug, "i": json.dumps(fields)})
        if result.rowcount == 0:
            raise RuntimeError(f"r96catidschema: category slug {slug!r} not found — is r43 applied?")


def downgrade() -> None:
    # r55's schemas are still the row's prior state in spirit, but merging
    # them back in is not worth the complexity for a schema-shape rollback;
    # the safe downgrade is the same "wipe to empty" r55 already used.
    #
    # (MUDD-123/L2) Say the consequence out loud, not just the decision: this
    # LOSES r55's field definitions instead of restoring them. After a rollback
    # every one of these categories has NO identifier fields — the sale form
    # stops asking for IMEI/VIN/serial altogether. Re-running `upgrade()` is
    # the way back; downgrading twice-over to r55's shapes is not available.
    # See the ⚠ block in this module's docstring.
    conn = op.get_bind()
    stmt = sa.text("UPDATE catalog_categories SET identifier_schema = '[]'::json WHERE slug = :slug")
    for slug in IDENTIFIERS:
        conn.execute(stmt, {"slug": slug})
