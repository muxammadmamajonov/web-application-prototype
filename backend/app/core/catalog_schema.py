"""The catalog schema contract — what a category declares, and who may set it.

``app/models/catalog.py`` has pointed here since r32 ("See app/core/catalog_schema.py
for the JSON shapes") but the module never existed: the shapes lived only in
``frontend/src/lib/catalogSchema.ts`` and in whatever each migration happened to
write. This is the backend half of that mirror, in the same posture
``app/core/catalog_identifiers.py`` holds against
``frontend/src/lib/catalogIdentifiers.ts`` — one contract, stated on both sides.

Two things live here and nowhere else:

**1. The shape of the two JSON columns.** ``CatalogCategory.attribute_schema``
and ``CatalogCategory.identifier_schema`` are hand-authored through
``CategorySchemaModal.tsx`` and stored verbatim: ``app/api/catalog.py``'s
``CategoryIn`` types both as a bare ``list``, so a malformed entry is accepted
by the API, silently dropped by ``parse_identifier_schema`` at read time, and
the platform admin is never told. A category can therefore *look* like it
requires an IMEI and enforce nothing. ``validate_identifier_schema`` /
``validate_attribute_schema`` turn that silence into a named problem.

**2. The defaults-versus-overrides contract.** ``filled_by`` already answers
"platform or tenant" for every attribute field — r55 wrote it into all 406 of
them (``"platform" if variant else "tenant"``), ``CatalogPage.tsx`` displays it
and ``CategorySchemaModal.tsx`` lets an admin change it. Nothing on the backend
has ever read it. ``tenant_may_supply`` is that reader. See
``docs/CATALOG_DEFAULTS_AND_OVERRIDES.md`` for the contract in prose.

Nothing here talks to the database. The integrity checks that need rows live in
``scripts/check_catalog_integrity.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.category_identifiers import MAX_VALUE_LENGTH

#: The attribute field types the category builder offers. Mirrors `FieldType`
#: in `frontend/src/lib/catalogSchema.ts`; every value is present in the
#: seeded catalog (r55 uses all six).
ATTRIBUTE_TYPES: frozenset[str] = frozenset(
    {"color", "enum", "number", "decimal", "text", "bool"}
)

#: The identifier value kinds. `number` is accepted for parity with the TS
#: `IdField['type']`; the seeded catalog uses only `text` and leans on
#: `validator` for the real shape check.
IDENTIFIER_TYPES: frozenset[str] = frozenset({"text", "number"})

#: The shape checks `app/core/catalog_identifiers.py::validate_catalog_identifier`
#: implements. A `validator` outside this set is not an error there — it falls
#: through to the bare length check — which is exactly why it is one HERE: a
#: typo'd `"imie"` silently downgrades an IMEI field to "any 15 characters".
IDENTIFIER_VALIDATORS: frozenset[str] = frozenset(
    {"imei", "vin", "mac", "serial", "inventory", "plate"}
)

FilledBy = Literal["platform", "tenant"]

#: A field with no `filled_by` is the tenant's to fill. Matches
#: `CategorySchemaModal.tsx`'s `field.filled_by ?? 'tenant'`, so a category
#: built by hand behaves the same as a seeded one.
DEFAULT_FILLED_BY: FilledBy = "tenant"

#: Both labels are required: the platform is bilingual and a field that renders
#: as a blank in one locale is a support ticket, not a cosmetic gap.
_REQUIRED_LABELS = ("label_uz", "label_ru")


@dataclass(frozen=True)
class SchemaProblem:
    """One thing wrong with one entry. ``index`` is the entry's position in the
    list so a caller can point at it; ``key`` is its declared key when it has a
    usable one."""

    index: int
    key: str | None
    code: str

    def __str__(self) -> str:  # pragma: no cover - diagnostic only
        where = f"[{self.index}]" + (f" ({self.key})" if self.key else "")
        return f"{where}: {self.code}"


class CatalogSchemaError(ValueError):
    """A category's declared schema is not usable. ``problems`` holds all of
    them — a caller fixing a hand-authored schema wants the whole list, not the
    first entry."""

    def __init__(self, problems: list[SchemaProblem]) -> None:
        self.problems = problems
        super().__init__("catalog_schema_invalid:" + ",".join(p.code for p in problems))


def _common_problems(entry: object, index: int, seen: set[str]) -> tuple[list[SchemaProblem], str | None]:
    """Checks every entry in either schema must pass: it is an object, it has a
    non-blank key, and that key is not a duplicate.

    Returns the problems plus the key, or ``None`` when the entry has no usable
    key — in which case the caller must stop rather than report a cascade of
    follow-on problems against an entry that has no identity.
    """
    if not isinstance(entry, dict):
        return [SchemaProblem(index, None, "entry_not_an_object")], None
    raw_key = entry.get("key")
    if not isinstance(raw_key, str) or not raw_key.strip():
        return [SchemaProblem(index, None, "missing_key")], None
    key = raw_key.strip()
    if key in seen:
        # `parse_identifier_schema` keeps the FIRST entry and drops the rest,
        # so a duplicate key does not shadow — it vanishes. Silently.
        return [SchemaProblem(index, key, "duplicate_key")], key
    seen.add(key)

    problems = [
        SchemaProblem(index, key, f"missing_{label}")
        for label in _REQUIRED_LABELS
        if not isinstance(entry.get(label), str) or not entry[label].strip()
    ]
    return problems, key


def validate_attribute_schema(raw: object) -> list[SchemaProblem]:
    """Check one category's ``attribute_schema`` and return every problem found.

    An empty list means the schema is usable — not that it is complete. "This
    category declares no variant axis" is a completeness question
    (``scripts/check_catalog_integrity.py`` asks it against real rows); this
    function only answers "is what is written here well-formed".
    """
    if not isinstance(raw, list):
        return [SchemaProblem(0, None, "schema_not_a_list")]

    problems: list[SchemaProblem] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        common, key = _common_problems(entry, index, seen)
        problems += common
        if key is None:
            continue
        assert isinstance(entry, dict)

        field_type = entry.get("type")
        if field_type not in ATTRIBUTE_TYPES:
            problems.append(SchemaProblem(index, key, "unknown_type"))

        for flag in ("variant", "required"):
            if flag in entry and not isinstance(entry[flag], bool):
                problems.append(SchemaProblem(index, key, f"{flag}_not_a_bool"))

        filled_by = entry.get("filled_by", DEFAULT_FILLED_BY)
        if filled_by not in ("platform", "tenant"):
            problems.append(SchemaProblem(index, key, "unknown_filled_by"))

        # An `enum` with no options renders as an empty dropdown the seller
        # cannot get past. `color` deliberately carries no `values` — it draws
        # on the `catalog_colors` table (see catalogSchema.ts).
        if field_type == "enum":
            values = entry.get("values")
            if not isinstance(values, list) or not values:
                problems.append(SchemaProblem(index, key, "enum_without_values"))
            else:
                problems += [
                    SchemaProblem(index, key, "enum_value_malformed")
                    for v in values
                    if not isinstance(v, dict) or not isinstance(v.get("value"), str)
                ][:1]

    return problems


def validate_identifier_schema(raw: object) -> list[SchemaProblem]:
    """Check one category's ``identifier_schema`` and return every problem found.

    Beyond the shape, this enforces the one invariant the two reader modules
    disagree about in silence: ``category_identifiers.py`` clamps any declared
    ``max_len`` to ``MAX_VALUE_LENGTH`` (the width of
    ``installment_item_identifiers.value``), while
    ``catalog_identifiers.py::validate_catalog_identifier`` reads it raw. A
    schema declaring more than the column holds is not rejected anywhere today
    — it is quietly enforced at 64 while telling the admin it allows more.
    """
    if not isinstance(raw, list):
        return [SchemaProblem(0, None, "schema_not_a_list")]

    problems: list[SchemaProblem] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        common, key = _common_problems(entry, index, seen)
        problems += common
        if key is None:
            continue
        assert isinstance(entry, dict)

        if entry.get("type") not in IDENTIFIER_TYPES:
            problems.append(SchemaProblem(index, key, "unknown_type"))

        if "required" in entry and not isinstance(entry["required"], bool):
            problems.append(SchemaProblem(index, key, "required_not_a_bool"))

        validator = entry.get("validator")
        if validator is not None and validator not in IDENTIFIER_VALIDATORS:
            problems.append(SchemaProblem(index, key, "unknown_validator"))

        min_len, max_len = entry.get("min_len"), entry.get("max_len")
        for name, bound in (("min_len", min_len), ("max_len", max_len)):
            if bound is not None and (not isinstance(bound, int) or isinstance(bound, bool) or bound <= 0):
                problems.append(SchemaProblem(index, key, f"{name}_not_a_positive_int"))

        if isinstance(max_len, int) and not isinstance(max_len, bool):
            if max_len > MAX_VALUE_LENGTH:
                problems.append(SchemaProblem(index, key, "max_len_exceeds_column"))
            if isinstance(min_len, int) and not isinstance(min_len, bool) and min_len > max_len:
                problems.append(SchemaProblem(index, key, "min_len_above_max_len"))

    return problems


def assert_valid_category_schemas(attribute_schema: object, identifier_schema: object) -> None:
    """Raise ``CatalogSchemaError`` if either schema is unusable.

    The write path (``POST``/``PATCH /catalog/categories``) is the place to call
    this: it is the only moment the author is still on the other end of the
    connection and can be told what is wrong.
    """
    problems = validate_attribute_schema(attribute_schema) + validate_identifier_schema(identifier_schema)
    if problems:
        raise CatalogSchemaError(problems)


# ── the defaults-versus-overrides contract ───────────────────────────────────

def filled_by(field: object) -> FilledBy:
    """Who supplies this attribute field's value. Unset means the tenant, the
    same default ``CategorySchemaModal.tsx`` applies."""
    if isinstance(field, dict) and field.get("filled_by") in ("platform", "tenant"):
        return field["filled_by"]  # type: ignore[return-value]
    return DEFAULT_FILLED_BY


def tenant_may_supply(field: object) -> bool:
    """May a tenant put a value for this field on their own ``Product``?

    ``filled_by == "platform"`` means the value belongs to the catalog model
    (``CatalogModel.attributes_template``) — the colours an iPhone 15 ships in
    are a fact about the product, identical in every shop, and a shop inventing
    its own set is how "Black" and "Qora" become two colours in the same
    rollup. Everything else is the tenant's to fill.
    """
    return filled_by(field) == "tenant"


def platform_owned_keys(attribute_schema: object) -> set[str]:
    """The attribute keys on this category a tenant may NOT set for themselves."""
    if not isinstance(attribute_schema, list):
        return set()
    return {
        f["key"].strip()
        for f in attribute_schema
        if isinstance(f, dict)
        and isinstance(f.get("key"), str)
        and f["key"].strip()
        and not tenant_may_supply(f)
    }


#: The category-level fields that are platform-global with no tenant override
#: path — see the doc for why each one is on this list rather than the next.
PLATFORM_GLOBAL_CATEGORY_FIELDS: frozenset[str] = frozenset(
    {"slug", "attribute_schema", "identifier_schema", "business_types"}
)

#: What a tenant controls instead. These are ``Product`` columns, not category
#: columns: a tenant does not override the catalog, they describe their own
#: stock against it.
TENANT_OWNED_PRODUCT_FIELDS: frozenset[str] = frozenset(
    {
        "name_uz", "name_ru", "description_uz", "description_ru",
        "sku", "base_price", "cost_price", "stock", "min_stock_level",
        "photo_urls", "attributes", "is_active",
    }
)
