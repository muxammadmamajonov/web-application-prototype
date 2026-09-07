"""Validators for `CatalogCategory.identifier_schema` fields.

Distinct from ``app/core/identifiers.py``, which governs the single free-text
``InstallmentItem.serial_number`` / ``identifier_type`` pair captured at sale
time. This module validates a value against one *catalog* identifier field
definition — the platform-defined per-unit ID fields a category exposes
(IMEI 1/2, VIN, serial, plate, inventory number, ...), each carrying its own
``validator`` key plus ``min_len`` / ``max_len`` bounds (see
``alembic/versions/r96_seed_identifier_validators.py`` for the seeded shapes).

IMEI reuses the Luhn/normalisation rules from ``app/core/identifiers.py`` so
the two never drift on what counts as a valid IMEI.

The client mirror, ``frontend/src/lib/catalogIdentifiers.ts``, and this module
both run every case in ``frontend/src/lib/catalogIdentifierVectors.json``
(see ``tests/test_mudd113_catalog_identifiers.py``) — currently 28 enumerated
cases — so drift on THOSE cases is caught on both sides, the same pattern
``identifiers.py`` / ``identifiers.ts`` / ``identifierVectors.json`` already
use. This is not a general drift guard: a validation rule added to only one
side, with no corresponding fixture case, is invisible to both suites. Adding
a rule means adding a vector for it here, not just editing the code.
"""
from __future__ import annotations

import re

from app.core.identifiers import _serial_chars_allowed, luhn_ok, normalize_imei, normalize_serial

IMEI_LENGTH = 15
VIN_LENGTH = 17

#: VIN excludes I, O, Q — they are visually confusable with 1/0 and were
#: dropped from the ISO 3779 alphabet for that reason.
_VIN_CHARSET = re.compile(r"^[A-HJ-NPR-Z0-9]+$")

_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}$")

#: A shop-assigned inventory tag: letters (any script — a shop's own tag is
#: as likely to be `ИНВ-001` as `INV-001`), digits, and separators only, via
#: the same `_serial_chars_allowed` charset `app/core/identifiers.py` uses —
#: no free-form text, so it stays scannable/typeable at the register.

#: A vehicle plate: letters, digits, spaces. Deliberately not pinned to one
#: country's exact grammar — plates vary by issue year/region even within
#: Uzbekistan, and a fake-strict pattern would reject real, valid plates.
_PLATE_RE = re.compile(r"^[A-Za-z0-9 ]+$")

ERR_REQUIRED = "catalog_identifier_required"
ERR_TOO_SHORT = "catalog_identifier_too_short"
ERR_TOO_LONG = "catalog_identifier_too_long"
ERR_IMEI_NOT_NUMERIC = "catalog_identifier_imei_not_numeric"
ERR_IMEI_LENGTH = "catalog_identifier_imei_length"
ERR_IMEI_CHECKSUM = "catalog_identifier_imei_checksum"
ERR_VIN_LENGTH = "catalog_identifier_vin_length"
ERR_VIN_CHARSET = "catalog_identifier_vin_charset"
ERR_MAC_FORMAT = "catalog_identifier_mac_format"
ERR_INVENTORY_FORMAT = "catalog_identifier_inventory_format"
ERR_PLATE_FORMAT = "catalog_identifier_plate_format"
ERR_UNKNOWN_FIELD = "catalog_identifier_unknown_field"


def validate_catalog_identifier(field: dict, raw: object) -> tuple[str | None, str | None]:
    """Validate ``raw`` against one ``identifier_schema`` field definition.

    Returns ``(normalized_value, error_code)`` — exactly one is set. A blank
    value on a non-required field is valid and means "not captured"; a blank
    value on a required field is rejected.
    """
    text = raw if isinstance(raw, str) else ""
    validator = field.get("validator")

    # IMEI is normalized by stripping scanner separators before length/charset
    # checks; every other kind uses the generic serial normalizer.
    value = normalize_imei(text) if validator == "imei" else normalize_serial(text)

    if not value:
        return (None, ERR_REQUIRED) if field.get("required") else (None, None)

    if validator == "imei":
        if not value.isdigit():
            return None, ERR_IMEI_NOT_NUMERIC
        if len(value) != IMEI_LENGTH:
            return None, ERR_IMEI_LENGTH
        if not luhn_ok(value):
            return None, ERR_IMEI_CHECKSUM
        return value, None

    if validator == "vin":
        vin = value.upper().replace(" ", "").replace("-", "")
        if len(vin) != VIN_LENGTH:
            return None, ERR_VIN_LENGTH
        if not _VIN_CHARSET.match(vin):
            return None, ERR_VIN_CHARSET
        return vin, None

    if validator == "mac":
        if not _MAC_RE.match(value):
            return None, ERR_MAC_FORMAT
        return value.upper(), None

    if validator == "inventory" and not _serial_chars_allowed(value):
        return None, ERR_INVENTORY_FORMAT
    if validator == "plate" and not _PLATE_RE.match(value):
        return None, ERR_PLATE_FORMAT
    # "serial" and unset validators: length bounds only. `normalize_serial`
    # does NOT restrict the character set by itself — only `_serial_chars_allowed`
    # (used above for "inventory") does that; a bare "serial"/unset field
    # accepts anything that survives invisible-character stripping.

    min_len = field.get("min_len")
    max_len = field.get("max_len")
    if min_len and len(value) < min_len:
        return None, ERR_TOO_SHORT
    if max_len and len(value) > max_len:
        return None, ERR_TOO_LONG
    return value, None


def find_identifier_field(identifier_schema: list, key: str) -> dict | None:
    """Look up one field definition by ``key`` in a category's identifier_schema."""
    for field in identifier_schema or []:
        if isinstance(field, dict) and field.get("key") == key:
            return field
    return None
