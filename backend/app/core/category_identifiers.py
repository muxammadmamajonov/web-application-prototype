"""Category-linked identifier fields (MUDD-85).

`catalog_categories.identifier_schema` declares the per-unit fields a category
wants captured at sale time — e.g. phones: IMEI 1 (required), IMEI 2
(optional); cars: VIN, engine number. It is untyped JSON, authored by a
platform admin through `CategorySchemaModal.tsx` and stored verbatim (see
`app/api/catalog.py`'s `CategoryIn.identifier_schema` — no server-side shape
validation happens there today). This module is the one place that reads that
JSON and turns a tenant's submission into a validated ``{key: value}`` payload
for an `InstallmentItem`. The values themselves live in
`installment_item_identifiers`, one row per (item, key), so a duplicate can be
rejected by a real per-tenant DB constraint instead of an application-only
race (see `app.models.installment.InstallmentItemIdentifier`).

Deliberately separate from `app.core.identifiers` (the single free-text
`serial_number` + `identifier_type` pair already on every item): that module
validates ONE generic identifier the seller chooses a kind for (IMEI vs a
shop's own serial). This module validates however many NAMED fields a
category declares. Neither the presence nor the shape of one implies anything
about the other — an item can carry both, either, or neither.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

#: Structured, translatable rejection codes — never shown to a user raw.
ERR_UNKNOWN_KEY = "category_identifier_unknown_key"
ERR_REQUIRED = "category_identifier_required"
ERR_TOO_LONG = "category_identifier_too_long"
ERR_NOT_NUMERIC = "category_identifier_not_numeric"

#: Agrees with `InstallmentItemIdentifier.value` (VARCHAR 64). A schema entry
#: may declare a tighter `max_len`; nothing may declare a looser one — a limit
#: the database does not share is a 500 waiting to happen.
MAX_VALUE_LENGTH = 64


@dataclass(frozen=True)
class IdentifierField:
    key: str
    required: bool
    numeric: bool
    max_len: int


def parse_identifier_schema(raw: object) -> list[IdentifierField]:
    """Defensive parse of a `catalog_categories.identifier_schema` value.

    The column is hand-authored JSON with no server-side shape validation
    (`app/api/catalog.py::CategoryIn`), so a malformed or half-written entry
    must not crash a tenant's contract creation — it is dropped instead, the
    same posture `_resolve_item_categories` takes toward a legacy
    `products.category` value that matches no real row.
    """
    if not isinstance(raw, list):
        return []
    fields: list[IdentifierField] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        if not isinstance(key, str) or not key.strip():
            continue
        key = key.strip()
        if key in seen:
            continue
        seen.add(key)
        max_len = entry.get("max_len")
        fields.append(IdentifierField(
            key=key,
            required=bool(entry.get("required", False)),
            numeric=entry.get("type") == "number",
            max_len=(
                max_len if isinstance(max_len, int) and 0 < max_len <= MAX_VALUE_LENGTH
                else MAX_VALUE_LENGTH
            ),
        ))
    return fields


def _normalize(raw: str) -> str:
    """NFC-compose, then trim and collapse internal whitespace runs.

    Mirrors `app.core.identifiers.normalize_serial` for the same reason: a
    value composed as one codepoint and one composed as two must store and
    compare identically, and a value copy-pasted with irregular spacing must
    not create a false "not a duplicate".
    """
    composed = unicodedata.normalize("NFC", raw)
    return " ".join(composed.split())


def normalize_identifier_value(raw: str) -> str:
    """Public name for the normalization `validate_category_identifiers` applies.

    (MUDD-123) A caller that needs to ask "is this submission the same value
    that is already stored?" has to compare like with like — the stored value
    went through `_normalize` on its way in, so a raw comparison would call a
    re-submitted, unchanged value "changed" over a stray NBSP. Exposed rather
    than reached into, so the answer cannot drift from what the writer did.
    """
    return _normalize(raw)


class CategoryIdentifierError(ValueError):
    """Submission failed schema validation — `errors` maps key -> error code.

    Only the first (key, code) is ever surfaced to the client (the API layer
    reports one field at a time, matching `_resolve_item_categories`'s
    `unknown_category:{slug}` convention) but every failing key is kept here
    for callers — tests included — that want the full picture.
    """

    def __init__(self, errors: dict[str, str]) -> None:
        self.errors = errors
        super().__init__(f"category_identifier_invalid:{','.join(sorted(errors))}")


def validate_category_identifiers(
    schema: list[IdentifierField], submitted: dict[str, str],
) -> dict[str, str]:
    """Normalize + enforce `schema` against `submitted`.

    Returns exactly the values that should be persisted: a blank optional
    field is dropped rather than stored as `""`, so its presence in the result
    always means "the seller recorded a value", not "the key exists". Raises
    `CategoryIdentifierError` if a required field is missing/blank, a key is
    not part of the schema at all (a category with no schema — or no category
    — therefore rejects everything sent), a value exceeds its field's
    `max_len`, or a `type: "number"` field holds a non-digit value.
    """
    errors: dict[str, str] = {}
    known_keys = {f.key for f in schema}
    unknown = sorted(set(submitted) - known_keys)
    if unknown:
        raise CategoryIdentifierError({k: ERR_UNKNOWN_KEY for k in unknown})

    result: dict[str, str] = {}
    for field in schema:
        raw_value = submitted.get(field.key)
        value = _normalize(raw_value) if isinstance(raw_value, str) else ""
        if not value:
            if field.required:
                errors[field.key] = ERR_REQUIRED
            continue
        if len(value) > field.max_len:
            errors[field.key] = ERR_TOO_LONG
            continue
        if field.numeric and not value.isdigit():
            errors[field.key] = ERR_NOT_NUMERIC
            continue
        result[field.key] = value

    if errors:
        raise CategoryIdentifierError(errors)
    return result
