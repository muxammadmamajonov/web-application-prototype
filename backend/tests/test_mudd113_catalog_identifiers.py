"""MUDD-113 — catalog identifier_schema: real, validated per-category fields.

Two things this pins:

1. ``app/core/catalog_identifiers.py`` actually rejects a malformed IMEI (bad
   Luhn digit, wrong length, non-numeric) and a malformed VIN (wrong length,
   a character ISO 3779 excludes) — not just accepts a free-text box that
   happens to be labelled "IMEI"/"VIN".
2. The seeded schema itself (r96catidschema) gives every one of the 114
   catalog categories at least one required + one optional identifier, and
   the migration is idempotent and actually lands in the database under RLS
   (r97catmodels is covered by the same ``migrated_db`` integration test).
"""
from __future__ import annotations

import pytest

from app.core.catalog_identifiers import (
    ERR_IMEI_CHECKSUM,
    ERR_IMEI_LENGTH,
    ERR_IMEI_NOT_NUMERIC,
    ERR_INVENTORY_FORMAT,
    ERR_MAC_FORMAT,
    ERR_PLATE_FORMAT,
    ERR_REQUIRED,
    ERR_TOO_LONG,
    ERR_TOO_SHORT,
    ERR_VIN_CHARSET,
    ERR_VIN_LENGTH,
    find_identifier_field,
    validate_catalog_identifier,
)

VALID_IMEI = "352099001761481"           # correct GSMA Luhn check digit
VALID_VIN = "1HGCM82633A004352"          # 17 chars, no I/O/Q

IMEI_FIELD = {"key": "imei1", "type": "text", "required": True,
              "validator": "imei", "min_len": 15, "max_len": 15}
VIN_FIELD = {"key": "vin", "type": "text", "required": True,
             "validator": "vin", "min_len": 17, "max_len": 17}
INVENTORY_FIELD = {"key": "inventory_no", "type": "text", "required": True,
                    "validator": "inventory", "min_len": 3, "max_len": 24}
MAC_FIELD = {"key": "mac", "type": "text", "required": False,
             "validator": "mac", "min_len": 17, "max_len": 17}
PLATE_FIELD = {"key": "plate", "type": "text", "required": False,
               "validator": "plate", "min_len": 6, "max_len": 12}
OPTIONAL_SERIAL_FIELD = {"key": "serial", "type": "text", "required": False,
                          "validator": "serial", "min_len": 4, "max_len": 40}


# ── IMEI (phones) ────────────────────────────────────────────────────────

def test_a_valid_imei_is_accepted() -> None:
    value, err = validate_catalog_identifier(IMEI_FIELD, VALID_IMEI)
    assert err is None
    assert value == VALID_IMEI


def test_an_imei_with_a_bad_check_digit_is_rejected() -> None:
    bad = VALID_IMEI[:-1] + str((int(VALID_IMEI[-1]) + 1) % 10)
    value, err = validate_catalog_identifier(IMEI_FIELD, bad)
    assert value is None
    assert err == ERR_IMEI_CHECKSUM


def test_a_14_digit_imei_is_rejected_as_too_short() -> None:
    value, err = validate_catalog_identifier(IMEI_FIELD, VALID_IMEI[:-1])
    assert value is None
    assert err == ERR_IMEI_LENGTH


def test_a_non_numeric_imei_is_rejected() -> None:
    value, err = validate_catalog_identifier(IMEI_FIELD, "35209900176148X")
    assert value is None
    assert err == ERR_IMEI_NOT_NUMERIC


def test_scanner_separators_are_stripped_before_checking() -> None:
    """A label reads e.g. `35-209900-176148-1` — normalize before validating."""
    spaced = "35-209900-176148-1"
    value, err = validate_catalog_identifier(IMEI_FIELD, spaced)
    assert err is None
    assert value == VALID_IMEI


def test_a_blank_required_imei_is_rejected() -> None:
    value, err = validate_catalog_identifier(IMEI_FIELD, "")
    assert value is None
    assert err == ERR_REQUIRED


# ── VIN (cars/motorcycles) ───────────────────────────────────────────────

def test_a_valid_vin_is_accepted() -> None:
    value, err = validate_catalog_identifier(VIN_FIELD, VALID_VIN)
    assert err is None
    assert value == VALID_VIN


def test_a_16_char_vin_is_rejected_as_wrong_length() -> None:
    value, err = validate_catalog_identifier(VIN_FIELD, VALID_VIN[:-1])
    assert value is None
    assert err == ERR_VIN_LENGTH


@pytest.mark.parametrize("excluded_char", ["I", "O", "Q"])
def test_a_vin_with_an_excluded_letter_is_rejected(excluded_char) -> None:
    """VIN drops I/O/Q — visually confusable with 1/0 — from ISO 3779."""
    poisoned = excluded_char + VALID_VIN[1:]
    value, err = validate_catalog_identifier(VIN_FIELD, poisoned)
    assert value is None
    assert err == ERR_VIN_CHARSET


def test_vin_is_case_and_separator_tolerant() -> None:
    value, err = validate_catalog_identifier(VIN_FIELD, VALID_VIN.lower())
    assert err is None
    assert value == VALID_VIN


# ── inventory tag (serialless categories) ────────────────────────────────

def test_a_valid_inventory_tag_is_accepted() -> None:
    value, err = validate_catalog_identifier(INVENTORY_FIELD, "INV-2026-014")
    assert err is None
    assert value == "INV-2026-014"


def test_an_inventory_tag_too_short_is_rejected() -> None:
    value, err = validate_catalog_identifier(INVENTORY_FIELD, "AB")
    assert value is None
    assert err == ERR_TOO_SHORT


def test_an_inventory_tag_with_illegal_characters_is_rejected() -> None:
    value, err = validate_catalog_identifier(INVENTORY_FIELD, "INV#2026!")
    assert value is None
    assert err == ERR_INVENTORY_FORMAT


@pytest.mark.parametrize("raw", ["ИНВ-001", "Инв 12", "INV 001"])
def test_an_inventory_tag_in_cyrillic_or_with_internal_space_is_accepted(raw) -> None:
    """(H2 regression) 68 of 114 seeded categories have no natural per-unit
    serial and use this as their REQUIRED field (r96's `INVENTORY_TAG`
    profile) — an ASCII-only charset locked a Russian-speaking shop out of
    ever completing a sale in those categories. `_serial_chars_allowed`
    (reused here, same as every other identifier module) accepts letters in
    any script plus the shop's own separators, including a plain space."""
    value, err = validate_catalog_identifier(INVENTORY_FIELD, raw)
    assert err is None
    assert value == raw


# ── plate (vehicles, optional) ───────────────────────────────────────────

def test_a_valid_plate_is_accepted() -> None:
    value, err = validate_catalog_identifier(PLATE_FIELD, "01A123BC")
    assert err is None
    assert value == "01A123BC"


def test_a_plate_with_illegal_characters_is_rejected() -> None:
    value, err = validate_catalog_identifier(PLATE_FIELD, "01-A123-BC")
    assert value is None
    assert err == ERR_PLATE_FORMAT


def test_a_blank_optional_plate_is_accepted_as_not_captured() -> None:
    value, err = validate_catalog_identifier(PLATE_FIELD, "")
    assert value is None
    assert err is None


# ── MAC (routers/cameras, optional) ──────────────────────────────────────

def test_a_valid_mac_is_accepted() -> None:
    value, err = validate_catalog_identifier(MAC_FIELD, "AA:BB:CC:11:22:33")
    assert err is None
    assert value == "AA:BB:CC:11:22:33"


def test_a_malformed_mac_is_rejected() -> None:
    value, err = validate_catalog_identifier(MAC_FIELD, "not-a-mac")
    assert value is None
    assert err == ERR_MAC_FORMAT


def test_a_blank_optional_mac_is_accepted_as_not_captured() -> None:
    value, err = validate_catalog_identifier(MAC_FIELD, "")
    assert value is None
    assert err is None


# ── generic serial length bounds ─────────────────────────────────────────

def test_a_serial_over_max_len_is_rejected() -> None:
    value, err = validate_catalog_identifier(OPTIONAL_SERIAL_FIELD, "S" * 41)
    assert value is None
    assert err == ERR_TOO_LONG


def test_an_optional_field_of_only_invisible_characters_normalizes_to_none_none() -> None:
    """(B1) A lone zero-width space is not "" by Python's own truthiness, so
    a caller must not assume "blank" is the only way to reach this branch —
    `app/api/tenant/installments.py::_validate_item_identifiers` used to
    `assert checked_value is not None` here and 500 on exactly this input."""
    value, err = validate_catalog_identifier(OPTIONAL_SERIAL_FIELD, "​")
    assert value is None
    assert err is None


def test_find_identifier_field_looks_up_by_key() -> None:
    schema = [IMEI_FIELD, OPTIONAL_SERIAL_FIELD]
    assert find_identifier_field(schema, "serial") == OPTIONAL_SERIAL_FIELD
    assert find_identifier_field(schema, "missing") is None


# ── the seeded schema shape (no DB needed — pure data) ───────────────────

def test_every_category_has_a_required_and_an_optional_identifier() -> None:
    """Owner requirement: every category needs >=1 required + >=1 optional
    identifier, each carrying a validator. Pinned against the migration's own
    data so a future edit that drops the balance fails immediately, without
    needing a database."""
    import importlib.util
    import pathlib

    path = (pathlib.Path(__file__).resolve().parents[1]
            / "alembic/versions/r96_seed_identifier_validators.py")
    spec = importlib.util.spec_from_file_location("r96catidschema", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert len(mod.IDENTIFIERS) == 114
    for slug, fields in mod.IDENTIFIERS.items():
        required = [f for f in fields if f["required"]]
        optional = [f for f in fields if not f["required"]]
        assert required, f"{slug}: no required identifier"
        assert optional, f"{slug}: no optional identifier"
        for field in fields:
            assert field.get("validator"), f"{slug}.{field['key']}: no validator"
            assert field.get("min_len") or field.get("max_len"), \
                f"{slug}.{field['key']}: no min/max bound"


# ── the shared vector contract ────────────────────────────────────────────
#
# `catalogIdentifierVectors.json` is run case-for-case by BOTH this suite and
# `frontend/src/lib/catalogIdentifiers.test.ts` — same pattern as
# `identifierVectors.json` / `identifiers.py` / `identifiers.ts`. Before this,
# the two files were "a backend test compares the two files so they cannot
# drift apart" only in a comment: no such test existed, so a hand-mirrored
# rule that only one side enforced would surface as the generic error screen
# after a round-trip instead of failing CI (MUDD-122 M3).

def _catalog_vectors() -> list[dict]:
    import json
    import pathlib

    path = (pathlib.Path(__file__).resolve().parents[2]
            / "frontend/src/lib/catalogIdentifierVectors.json")
    return json.loads(path.read_text())["cases"]


@pytest.mark.parametrize("vector", _catalog_vectors(), ids=lambda v: v["name"])
def test_shared_catalog_vectors_hold_on_the_server(vector) -> None:
    """Every case here is run byte-for-byte by the client suite as well."""
    value, error = validate_catalog_identifier(vector["field"], vector["input"])
    assert value == vector["value"], vector["name"]
    assert error == vector["error"], vector["name"]


def test_the_catalog_vector_file_covers_every_validator() -> None:
    """A shared fixture nobody extends stops being a contract."""
    validators = {v["field"].get("validator") for v in _catalog_vectors()}
    assert validators == {"imei", "vin", "inventory", "mac", "plate", "serial"}
    assert len(_catalog_vectors()) >= 20


# ── integration: the migration actually lands in the database ────────────

@pytest.mark.asyncio
async def test_migration_writes_real_identifier_schemas_under_rls(migrated_db) -> None:
    """(#RLS) SELECT under this repo's RLS returns an empty result silently —
    it does not error. Reading back through a real async session (not just
    the alembic sync connection that wrote it) is the only way to prove the
    seed is visible to the app, not just present in the migration's own
    transaction."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models import CatalogCategory

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        phones = (await s.execute(
            select(CatalogCategory).where(CatalogCategory.slug == "smartfonlar")
        )).scalar_one()
        cars = (await s.execute(
            select(CatalogCategory).where(CatalogCategory.slug == "avtomobillar")
        )).scalar_one()

    imei_field = find_identifier_field(phones.identifier_schema, "imei1")
    assert imei_field is not None
    assert imei_field["required"] is True
    assert imei_field["validator"] == "imei"

    vin_field = find_identifier_field(cars.identifier_schema, "vin")
    assert vin_field is not None
    assert vin_field["required"] is True
    assert vin_field["validator"] == "vin"

    # A bad IMEI submitted against the seeded phone schema is rejected...
    _, err = validate_catalog_identifier(imei_field, VALID_IMEI[:-1])
    assert err == ERR_IMEI_LENGTH
    # ...and a bad VIN against the seeded car schema is rejected too.
    poisoned_vin = "I" + VALID_VIN[1:]  # 17 chars, wrong length check can't mask this
    _, err = validate_catalog_identifier(vin_field, poisoned_vin)
    assert err == ERR_VIN_CHARSET


@pytest.mark.asyncio
async def test_migration_seeds_models_with_variant_templates(migrated_db) -> None:
    """Part B: models exist and carry the category's variant axes, not just a
    bare name — proves r97catmodels actually wrote rows, read back under RLS."""
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models import CatalogModel

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        total = (await s.execute(select(func.count()).select_from(CatalogModel))).scalar_one()
        iphone15 = (await s.execute(
            select(CatalogModel).where(CatalogModel.slug == "iphone-15")
        )).scalar_one()

    assert total >= 120
    assert iphone15.attributes_template["storage"] == [128, 256, 512]
    assert "black" in iphone15.attributes_template["color"]


@pytest.mark.asyncio
async def test_r97catmodels_upgrade_is_idempotent_when_run_twice(migrated_db) -> None:
    """(M3) `migrated_db` already ran r97catmodels's `upgrade()` once via
    `alembic upgrade head`. This calls the SAME function a second time,
    directly, with no downgrade in between — the exact scenario a retried CI
    step or a re-run migration produces — and asserts the row count does not
    move and nothing raises. Proves the `ON CONFLICT (brand_id, slug) WHERE
    tenant_id IS NULL DO NOTHING` guard in code, not just by inspection.
    """
    import importlib.util
    import pathlib

    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine, func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.config import settings
    from app.models import CatalogModel

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        before = (await s.execute(select(func.count()).select_from(CatalogModel))).scalar_one()
    assert before >= 120  # sanity: the first (fixture-driven) upgrade did seed rows

    path = (pathlib.Path(__file__).resolve().parents[1]
            / "alembic/versions/r97_seed_catalog_models.py")
    spec = importlib.util.spec_from_file_location("r97catmodels_rerun", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    sync_engine = create_engine(settings.migration_url_sync)
    try:
        with sync_engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mod.upgrade()  # second run — no error, no duplicate rows
            conn.commit()
    finally:
        sync_engine.dispose()

    async with Session() as s:
        after = (await s.execute(select(func.count()).select_from(CatalogModel))).scalar_one()
    assert after == before
