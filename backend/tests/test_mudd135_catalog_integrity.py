"""MUDD-135: the catalog's schema contract, and the references that hold it up.

Two tiers, and the split matters.

The **unit** tier pins `app/core/catalog_schema.py` — the shape rules for the
two hand-authored JSON columns. It also pins the rules against the *other* two
places the same contract is written down (`frontend/src/lib/catalogSchema.ts`
and `app/core/catalog_identifiers.py`), because a contract stated three times
and checked in one is a contract that will drift.

The **integration** tier runs against a really-migrated database and does the
thing the previous rounds did not: it damages the catalog the exact way the
product can damage it, then asserts the damage is refused, detected, and — where
there is one right answer — repaired. A check that has only ever been run
against clean data has not been shown to detect anything.
"""
from __future__ import annotations

import json
import pathlib

import pytest
import pytest_asyncio
import sqlalchemy as sa

from app.core.catalog_schema import (
    ATTRIBUTE_TYPES,
    DEFAULT_FILLED_BY,
    IDENTIFIER_VALIDATORS,
    CatalogSchemaError,
    assert_valid_category_schemas,
    filled_by,
    platform_owned_keys,
    tenant_may_supply,
    validate_attribute_schema,
    validate_identifier_schema,
)
from app.core.category_identifiers import MAX_VALUE_LENGTH

_REPO = pathlib.Path(__file__).resolve().parents[2]


def _codes(problems) -> set[str]:
    return {p.code for p in problems}


# ══ unit: the shape contract ═════════════════════════════════════════════════

#: The shapes r55categoryschemas / r96catidschema actually seed, copied from a
#: migrated database. If the validator rejects these it is the validator that is
#: wrong — the catalog has shipped with them for months.
SEEDED_ATTRS = [
    {"key": "color", "label_uz": "Rang", "label_ru": "Цвет", "type": "color",
     "variant": True, "required": False, "filled_by": "platform"},
    {"key": "condition", "label_uz": "Holati", "label_ru": "Состояние", "type": "enum",
     "variant": True, "required": False, "filled_by": "platform",
     "values": [{"value": "new", "label_uz": "Yangi", "label_ru": "Новый"}]},
    {"key": "screen", "label_uz": "Ekran", "label_ru": "Диагональ", "type": "decimal",
     "variant": False, "required": False, "filled_by": "tenant", "unit": "dyuym"},
]

SEEDED_IDS = [
    {"key": "imei1", "label_uz": "IMEI 1", "label_ru": "IMEI 1", "type": "text",
     "required": True, "min_len": 15, "max_len": 15, "validator": "imei"},
    {"key": "serial", "label_uz": "Seriya raqami", "label_ru": "Серийный номер",
     "type": "text", "required": False, "min_len": 4, "max_len": 40, "validator": "serial"},
]


def test_the_shapes_the_catalog_actually_ships_are_accepted():
    assert validate_attribute_schema(SEEDED_ATTRS) == []
    assert validate_identifier_schema(SEEDED_IDS) == []
    assert_valid_category_schemas(SEEDED_ATTRS, SEEDED_IDS)  # does not raise


def test_an_empty_schema_is_well_formed_even_though_it_enforces_nothing():
    """"Empty" and "malformed" are different answers. A category with no
    identifiers is a completeness question for `check_catalog_integrity`, not a
    shape error — conflating them would make the shape check unusable as a
    write-path guard."""
    assert validate_attribute_schema([]) == []
    assert validate_identifier_schema([]) == []


@pytest.mark.parametrize("raw", [None, {}, "[]", 7])
def test_a_schema_that_is_not_a_list_is_rejected(raw):
    assert _codes(validate_identifier_schema(raw)) == {"schema_not_a_list"}
    assert _codes(validate_attribute_schema(raw)) == {"schema_not_a_list"}


@pytest.mark.parametrize("entry,expected", [
    ("not-an-object", "entry_not_an_object"),
    ({"label_uz": "x", "label_ru": "x", "type": "text"}, "missing_key"),
    ({"key": "   ", "label_uz": "x", "label_ru": "x", "type": "text"}, "missing_key"),
    ({"key": "k", "label_ru": "x", "type": "text"}, "missing_label_uz"),
    ({"key": "k", "label_uz": "x", "type": "text"}, "missing_label_ru"),
])
def test_entries_without_an_identity_or_a_label_are_rejected(entry, expected):
    assert expected in _codes(validate_identifier_schema([entry]))


def test_a_duplicate_key_is_reported_because_the_reader_silently_drops_it():
    """`parse_identifier_schema` keeps the first entry and drops later ones with
    the same key. The second field does not shadow the first — it disappears,
    with nothing said to the admin who wrote it."""
    dup = dict(SEEDED_IDS[0])
    assert "duplicate_key" in _codes(validate_identifier_schema([SEEDED_IDS[0], dup]))


def test_a_typod_validator_is_rejected_because_it_silently_disables_the_check():
    """`validate_catalog_identifier` falls through on an unknown `validator` to a
    bare length check. So `"imie"` does not fail loudly — it downgrades an IMEI
    field to "any 15 characters", Luhn and all."""
    bad = dict(SEEDED_IDS[0], validator="imie")
    assert "unknown_validator" in _codes(validate_identifier_schema([bad]))


def test_a_max_len_wider_than_the_column_is_rejected():
    """`category_identifiers.py` clamps any declared `max_len` to
    MAX_VALUE_LENGTH, so a schema saying 200 enforces 64 while telling the admin
    it allows 200. Rejecting it is the only way the admin ever finds out."""
    bad = dict(SEEDED_IDS[1], max_len=MAX_VALUE_LENGTH + 1)
    assert "max_len_exceeds_column" in _codes(validate_identifier_schema([bad]))
    ok = dict(SEEDED_IDS[1], max_len=MAX_VALUE_LENGTH)
    assert validate_identifier_schema([ok]) == []


@pytest.mark.parametrize("entry,expected", [
    ({**SEEDED_IDS[1], "min_len": 41, "max_len": 40}, "min_len_above_max_len"),
    ({**SEEDED_IDS[1], "max_len": 0}, "max_len_not_a_positive_int"),
    ({**SEEDED_IDS[1], "min_len": "4"}, "min_len_not_a_positive_int"),
    ({**SEEDED_IDS[1], "type": "date"}, "unknown_type"),
    ({**SEEDED_IDS[1], "required": "yes"}, "required_not_a_bool"),
])
def test_identifier_bound_and_type_problems(entry, expected):
    assert expected in _codes(validate_identifier_schema([entry]))


@pytest.mark.parametrize("entry,expected", [
    ({**SEEDED_ATTRS[0], "type": "currency"}, "unknown_type"),
    ({**SEEDED_ATTRS[0], "variant": "yes"}, "variant_not_a_bool"),
    ({**SEEDED_ATTRS[0], "filled_by": "nobody"}, "unknown_filled_by"),
    ({"key": "k", "label_uz": "x", "label_ru": "x", "type": "enum"}, "enum_without_values"),
    ({"key": "k", "label_uz": "x", "label_ru": "x", "type": "enum", "values": []},
     "enum_without_values"),
    ({"key": "k", "label_uz": "x", "label_ru": "x", "type": "enum", "values": [{"label_uz": "a"}]},
     "enum_value_malformed"),
])
def test_attribute_shape_problems(entry, expected):
    assert expected in _codes(validate_attribute_schema([entry]))


def test_a_color_field_needs_no_values_because_the_palette_is_a_table():
    """`catalog_colors` holds ~106 rows and `GET /catalog/colors` serves them.
    A `color` field carrying its own list would be a copy of a catalogue, which
    `catalogSchema.ts` removed for exactly that reason."""
    assert validate_attribute_schema([SEEDED_ATTRS[0]]) == []


def test_assert_valid_raises_with_every_problem_not_just_the_first():
    with pytest.raises(CatalogSchemaError) as exc:
        assert_valid_category_schemas(
            [{"key": "a", "label_uz": "x", "label_ru": "x", "type": "nope"}],
            [{"key": "b", "label_uz": "x", "label_ru": "x", "type": "text", "max_len": 999}],
        )
    assert _codes(exc.value.problems) == {"unknown_type", "max_len_exceeds_column"}


# ══ unit: the defaults-versus-overrides contract ═════════════════════════════

def test_filled_by_defaults_to_tenant_exactly_as_the_category_builder_does():
    """`CategorySchemaModal.tsx` renders `field.filled_by ?? 'tenant'`. A
    hand-built category must behave like a seeded one, so the backend default
    has to be the same value — not "platform", and not "unset means reject"."""
    assert filled_by({"key": "k"}) == DEFAULT_FILLED_BY == "tenant"
    assert tenant_may_supply({"key": "k"}) is True


def test_a_platform_field_is_not_the_tenants_to_fill():
    assert tenant_may_supply(SEEDED_ATTRS[0]) is False   # color, filled_by=platform
    assert tenant_may_supply(SEEDED_ATTRS[2]) is True    # screen, filled_by=tenant
    assert platform_owned_keys(SEEDED_ATTRS) == {"color", "condition"}


def test_platform_owned_keys_survives_a_malformed_schema():
    """This is read on a live request path against a column with no write-time
    validation, so it must degrade rather than raise."""
    assert platform_owned_keys(None) == set()
    assert platform_owned_keys([1, {"key": ""}, {"no": "key"}]) == set()


# ══ unit: the three copies of one contract must agree ════════════════════════

def test_attribute_types_match_the_typescript_mirror():
    """`frontend/src/lib/catalogSchema.ts` declares the same six types for the
    category builder. If one side gains a type the other rejects, an admin can
    save a category the server refuses to read."""
    ts = (_REPO / "frontend/src/lib/catalogSchema.ts").read_text(encoding="utf-8")
    line = next(x for x in ts.splitlines() if x.startswith("export type FieldType"))
    declared = {chunk.strip().strip("';\"") for chunk in line.split("=", 1)[1].split("|")}
    assert declared == set(ATTRIBUTE_TYPES)


def test_identifier_validators_match_the_module_that_implements_them():
    """Every name in `IDENTIFIER_VALIDATORS` must be a branch
    `validate_catalog_identifier` actually takes. A name here that it does not
    implement would be accepted by the shape check and enforce nothing."""
    source = (_REPO / "backend/app/core/catalog_identifiers.py").read_text(encoding="utf-8")
    for name in IDENTIFIER_VALIDATORS:
        assert f'"{name}"' in source, f"{name} is declared but not implemented"


# ══ integration: the shipped catalog, and the damage it can take ═════════════

pytestmark_db = pytest.mark.asyncio


def _sync_url() -> str:
    from app.config import settings
    return settings.migration_url_sync.replace("+asyncpg", "+psycopg")


@pytest_asyncio.fixture
async def catalog_db(migrated_db):
    """A really-migrated database, plus a synchronous engine on the same one.

    The check and repair scripts are synchronous by design (they run as
    operational tooling against a production URL), so they are exercised here
    exactly as an operator would run them rather than through an async shim
    that would not prove the real path works.
    """
    engine = sa.create_engine(_sync_url())
    try:
        yield engine
    finally:
        engine.dispose()


def _check(engine) -> dict:
    from scripts.check_catalog_integrity import collect
    with engine.connect() as conn:
        conn.execute(sa.text("SET TRANSACTION READ ONLY"))
        return collect(conn)


def _repair(engine, *, apply: bool) -> dict:
    from scripts.seed_catalog import run
    if apply:
        with engine.begin() as conn:
            return run(conn, apply=True)
    with engine.connect() as conn:
        conn.execute(sa.text("SET TRANSACTION READ ONLY"))
        return run(conn, apply=False)


@pytest.mark.asyncio
async def test_the_shipped_catalog_has_no_integrity_errors(catalog_db):
    """The regression guard on the seed itself: r43–r55/r96/r97 must leave a
    catalog with nothing dangling, nothing duplicated, nothing orphaned."""
    report = _check(catalog_db)
    assert report["errors"] == [], report["errors"][:5]
    # Sanity that the check ran against a populated catalog and not an empty one
    # — "zero errors" on zero rows would be a green light that means nothing.
    assert report["counts"]["categories"] > 100
    assert report["counts"]["brands"] > 500


@pytest.mark.asyncio
async def test_every_shipped_category_schema_is_well_formed(catalog_db):
    """Runs the new shape rules over all 114 seeded categories. Both directions
    matter: a failure here is either a bad seed or a validator stricter than the
    contract it claims to describe."""
    with catalog_db.connect() as conn:
        rows = conn.execute(sa.text(
            "SELECT slug, attribute_schema, identifier_schema FROM catalog_categories"
        )).all()
    assert rows, "catalog_categories is empty — the migration tier did not run"
    bad = {}
    for slug, attrs, ids in rows:
        attrs = json.loads(attrs) if isinstance(attrs, str) else attrs
        ids = json.loads(ids) if isinstance(ids, str) else ids
        problems = validate_attribute_schema(attrs) + validate_identifier_schema(ids)
        if problems:
            bad[slug] = [str(p) for p in problems]
    assert bad == {}


@pytest.mark.asyncio
async def test_every_category_declares_at_least_one_variant_axis(catalog_db):
    """A category with no `variant` field cannot express a SKU: every unit of
    every model looks identical, so colour/size/storage can never be recorded.
    All 114 seeded categories carry one; this holds that."""
    report = _check(catalog_db)
    missing = [n for n in report["notes"] if n["check"] == "category_without_variant_axis"]
    assert missing == []


@pytest.mark.asyncio
async def test_deleting_a_category_that_still_has_brands_is_refused(catalog_db):
    """The headline hole. Before `rl0catintegrity`, this DELETE succeeded and
    took every brand whose primary category it was — with all of their models —
    out of every OTHER category those brands served, silently. Measured on a
    fresh database: deleting `planshetlar` dropped 921 brands to 916 and 125
    models to 117, and raised nothing.

    The category is chosen as one with brands but **no models**, so only the
    brands FK can refuse the delete. Pinning this to `planshetlar` (13 models)
    made the test pass with the brands FK mutated back to CASCADE — the models
    FK was refusing it, and the test could not tell the difference.
    """
    with catalog_db.connect() as conn:
        victim = conn.execute(sa.text("""
            SELECT c.slug FROM catalog_categories c
             WHERE EXISTS (SELECT 1 FROM catalog_brands b WHERE b.category_id = c.id)
               AND NOT EXISTS (SELECT 1 FROM catalog_models m WHERE m.category_id = c.id)
             ORDER BY c.slug LIMIT 1
        """)).scalar_one()
        before = conn.execute(sa.text("SELECT count(*) FROM catalog_brands")).scalar_one()
    with pytest.raises(sa.exc.IntegrityError), catalog_db.begin() as conn:
        conn.execute(sa.text("DELETE FROM catalog_categories WHERE slug = :s"),
                     {"s": victim})
    with catalog_db.connect() as conn:
        after = conn.execute(sa.text("SELECT count(*) FROM catalog_brands")).scalar_one()
    assert after == before


@pytest.mark.asyncio
async def test_deleting_a_category_that_still_has_models_is_refused(catalog_db):
    """The other half of the same guard, pinned to the models FK: a category
    with models can no longer be deleted either. Before `rl0catintegrity` this
    `SET NULL`'d their `category_id`, leaving a model that resolves no
    `identifier_schema` and is filtered out of every category-scoped picker."""
    with catalog_db.connect() as conn:
        orphaned_before = conn.execute(sa.text(
            "SELECT count(*) FROM catalog_models WHERE category_id IS NULL"
        )).scalar_one()
        # Mirror of the isolation above: a category with models but **no**
        # brands naming it as primary, so only the models FK can refuse. On the
        # seeded catalog that is `televizorlar` (15 models, 0 primary brands).
        victim = conn.execute(sa.text("""
            SELECT c.slug FROM catalog_categories c
             WHERE EXISTS (SELECT 1 FROM catalog_models m WHERE m.category_id = c.id)
               AND NOT EXISTS (SELECT 1 FROM catalog_brands b WHERE b.category_id = c.id)
             ORDER BY c.slug LIMIT 1
        """)).scalar_one()
    with pytest.raises(sa.exc.IntegrityError), catalog_db.begin() as conn:
        conn.execute(sa.text("DELETE FROM catalog_categories WHERE slug = :s"),
                     {"s": victim})
    with catalog_db.connect() as conn:
        assert conn.execute(sa.text(
            "SELECT count(*) FROM catalog_models WHERE category_id IS NULL"
        )).scalar_one() == orphaned_before


@pytest.mark.asyncio
async def test_deleting_an_unreferenced_category_still_works(catalog_db):
    """The guard must be exactly as wide as the hole. Removing a category that
    nothing points at — a typo'd one, the common real case — is still allowed;
    only deleting one that is in use is not."""
    with catalog_db.begin() as conn:
        conn.execute(sa.text("""
            INSERT INTO catalog_categories
                (id, slug, name_uz, name_ru, attribute_schema, identifier_schema,
                 business_types, sort_order, is_active, created_at, updated_at)
            VALUES (gen_random_uuid(), 'zz-unreferenced', 'T', 'Т',
                    '[]'::json, '[]'::json, '[]'::json, 999, true, now(), now())
        """))
        deleted = conn.execute(sa.text(
            "DELETE FROM catalog_categories WHERE slug = 'zz-unreferenced'"
        )).rowcount
    assert deleted == 1


@pytest.mark.asyncio
async def test_deactivating_a_category_with_models_does_not_red_the_gate(catalog_db):
    """The retirement path this migration prescribes in place of `DELETE` must
    not turn the deploy gate it ships into a permanent failure. Measured before
    this fix: deactivating one live category (`is_active = false`, one query)
    turned `ERRORS: 0` into `ERRORS: 41` — one `model_under_inactive_parent` per
    live model still under it — with no automated repair, since `seed_catalog`'s
    R1–R4 never touch `is_active`. The check must classify this as a note, so
    the first legitimate retirement does not red the gate.
    """
    with catalog_db.connect() as conn:
        victim = conn.execute(sa.text("""
            SELECT c.slug FROM catalog_categories c
             WHERE EXISTS (SELECT 1 FROM catalog_models m
                            WHERE m.category_id = c.id AND m.is_active)
             ORDER BY c.slug LIMIT 1
        """)).scalar_one()
    before = _check(catalog_db)
    assert before["errors"] == []

    with catalog_db.begin() as conn:
        conn.execute(sa.text(
            "UPDATE catalog_categories SET is_active = false WHERE slug = :s"
        ), {"s": victim})

    after = _check(catalog_db)
    assert after["errors"] == [], (
        "deactivating a category reds the gate — its own supported retirement "
        "path must not be misclassified as an integrity error"
    )
    notes = [n for n in after["notes"] if n["check"] == "model_under_inactive_parent"]
    assert notes, "the deactivation should still surface, just as a note"


@pytest.mark.asyncio
async def test_the_same_brand_cannot_exist_as_two_rows(catalog_db):
    """`category_ids` is the source of truth, so one brand is one row. The old
    `(category_id, slug)` constraint allowed one Apple per primary category —
    the pre-`category_ids` shape, in which a brand's models split across rows
    that no query joins back together."""
    with pytest.raises(sa.exc.IntegrityError), catalog_db.begin() as conn:
        conn.execute(sa.text("""
                INSERT INTO catalog_brands
                    (id, slug, name, category_ids, sort_order, is_active, created_at, updated_at)
                VALUES (gen_random_uuid(), 'apple', 'Apple (duplicate)',
                        '[]'::jsonb, 100, true, now(), now())
            """))


@pytest.mark.asyncio
async def test_the_check_detects_damage_it_is_supposed_to_detect(catalog_db):
    """A check only ever run against clean data has not been shown to detect
    anything. Each of these is written the way the product would write it."""
    baseline = _check(catalog_db)
    assert baseline["errors"] == []

    with catalog_db.begin() as conn:
        # A dangling category_ids entry — what `DELETE /catalog/categories/{id}`
        # leaves on every brand that also served the deleted category.
        conn.execute(sa.text("""
            UPDATE catalog_brands
               SET category_ids = category_ids || to_jsonb(gen_random_uuid()::text)
             WHERE slug = 'apple'
        """))
        # An orphaned model — what the old `SET NULL` FK produced.
        conn.execute(sa.text("""
            UPDATE catalog_models SET category_id = NULL
             WHERE slug = 'iphone-13'
        """))
        # A schema the readers will silently drop.
        conn.execute(sa.text("""
            UPDATE catalog_categories
               SET identifier_schema = '[{"key": "x", "type": "text", "validator": "imie"}]'::json
             WHERE slug = 'gilamlar'
        """))

    found = {e["check"] for e in _check(catalog_db)["errors"]}
    assert "brand_category_ids_dangling" in found
    assert "model_without_category" in found
    assert "category_schema_malformed" in found


@pytest.mark.asyncio
async def test_the_repair_fixes_what_it_claims_and_refuses_what_it_cannot_know(catalog_db):
    """R1/R2/R3 restore invariants with one correct answer. R4 re-points an
    orphaned model only when its brand serves exactly one category — an orphaned
    Apple model could be an iPad or an iPhone, and filing it under a plausible
    guess is how a catalog stops being worth trusting.
    """
    with catalog_db.begin() as conn:
        ghost = conn.execute(sa.text("SELECT gen_random_uuid()::text")).scalar_one()
        # R1: a brand carrying a category id that does not exist.
        conn.execute(sa.text(
            # `to_jsonb` is polymorphic, so the argument's type has to be known
            # at plan time; a bound parameter arrives as `unknown` and the cast
            # is what lets Postgres resolve it. The migration hits the typed
            # form (`to_jsonb(b.category_id::text)`) and never needs this.
            "UPDATE catalog_brands SET category_ids = category_ids || to_jsonb(CAST(:g AS text)) "
            "WHERE slug = 'apple'"), {"g": ghost})
        # R2: a live primary the brand's own category_ids omits.
        conn.execute(sa.text("""
            UPDATE catalog_brands
               SET category_ids = (SELECT jsonb_agg(x) FROM jsonb_array_elements(category_ids) x
                                    WHERE x <> to_jsonb(category_id::text))
             WHERE slug = 'samsung' AND category_id IS NOT NULL
        """))
        # R4-repairable: an orphan under a single-category brand.
        single = conn.execute(sa.text("""
            SELECT m.id FROM catalog_models m JOIN catalog_brands b ON b.id = m.brand_id
             WHERE jsonb_array_length(b.category_ids) = 1 LIMIT 1
        """)).scalar_one()
        conn.execute(sa.text("UPDATE catalog_models SET category_id = NULL WHERE id = :i"),
                     {"i": single})
        # R4-ambiguous: an orphan under a multi-category brand.
        multi = conn.execute(sa.text("""
            SELECT m.id FROM catalog_models m JOIN catalog_brands b ON b.id = m.brand_id
             WHERE jsonb_array_length(b.category_ids) > 1 LIMIT 1
        """)).scalar_one()
        conn.execute(sa.text("UPDATE catalog_models SET category_id = NULL WHERE id = :i"),
                     {"i": multi})

    dry = _repair(catalog_db, apply=False)
    by_code = {r["code"]: r for r in dry["repairs"]}
    assert by_code["R1"]["found"] == 1
    assert by_code["R2"]["found"] == 1
    assert by_code["R4"]["found"] == 1          # only the unambiguous one
    assert by_code["R1"]["changed"] == 0, "a dry run must not write"
    assert [a["id"] for a in dry["ambiguous"]] == [str(multi)]

    applied = _repair(catalog_db, apply=True)
    assert applied["changed"] >= 3

    with catalog_db.connect() as conn:
        assert conn.execute(sa.text(
            "SELECT category_ids ? :g FROM catalog_brands WHERE slug = 'apple'"),
            {"g": ghost}).scalar_one() is False
        assert conn.execute(sa.text(
            "SELECT category_ids ? category_id::text FROM catalog_brands WHERE slug = 'samsung'"
        )).scalar_one() is True
        assert conn.execute(sa.text(
            "SELECT category_id IS NOT NULL FROM catalog_models WHERE id = :i"),
            {"i": single}).scalar_one() is True
        # The ambiguous one is untouched — reported, never guessed.
        assert conn.execute(sa.text(
            "SELECT category_id IS NULL FROM catalog_models WHERE id = :i"),
            {"i": multi}).scalar_one() is True


@pytest.mark.asyncio
async def test_repair_is_idempotent(catalog_db):
    """"Re-running it must be a no-op" — the property the old upsert-by-slug
    seeder claimed and could not demonstrate, because it could not run at all."""
    with catalog_db.begin() as conn:
        conn.execute(sa.text(
            "UPDATE catalog_brands SET category_ids = category_ids || "
            "to_jsonb(gen_random_uuid()::text) WHERE slug = 'apple'"))

    first = _repair(catalog_db, apply=True)
    assert first["changed"] == 1
    second = _repair(catalog_db, apply=True)
    assert second["changed"] == 0
    assert all(r["found"] == 0 for r in second["repairs"])


@pytest.mark.asyncio
async def test_migration_upgrade_is_idempotent(catalog_db):
    """Re-running `rl0catintegrity.upgrade()` against an already-upgraded
    database must add nothing and raise nothing — the constraint work is guarded
    on `pg_constraint`, the heal on "still broken"."""
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = _REPO / "backend/alembic/versions/rl0catintegrity_catalog_referential_integrity.py"
    spec = importlib.util.spec_from_file_location("_rl0catintegrity", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with catalog_db.connect() as conn:
        before = conn.execute(sa.text("SELECT count(*) FROM catalog_brands")).scalar_one()

    with catalog_db.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            module.upgrade()

    with catalog_db.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM catalog_brands")).scalar_one() == before
        actions = dict(conn.execute(sa.text("""
            SELECT conname, confdeltype FROM pg_constraint
             WHERE conname IN ('fk_catalog_brands_category_id_catalog_categories',
                               'fk_catalog_models_category_id_catalog_categories')
        """)).all())
    assert set(actions.values()) == {"r"}, "both FKs must still be RESTRICT"


# ---------------------------------------------------------------------------
# The check script's own credential. r99 stripped the restricted runtime login
# (muddato_api) of every right on the catalog tables, and CI and Fly both bind
# DATABASE_URL_SYNC to exactly that login. A resolver that reaches it before the
# migration credential makes the documented operator command die with
# "permission denied for table catalog_brands" — reproduced by hand before this
# test existed. Pin the order so it cannot drift back.
# ---------------------------------------------------------------------------
_RUNTIME = "postgresql+psycopg://muddato_api:pw@localhost:5432/postgres"
_MIGRATION = "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"
_EXPLICIT = "postgresql+psycopg://explicit:pw@localhost:5432/postgres"
_URL_VARS = (
    "CATALOG_CHECK_DATABASE_URL",
    "MIGRATION_DATABASE_URL_SYNC",
    "DATABASE_URL_SYNC",
)


def _resolve(monkeypatch, **env: str) -> str:
    from scripts.check_catalog_integrity import _resolve_url

    for var in _URL_VARS:
        monkeypatch.delenv(var, raising=False)
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    return _resolve_url(env.pop("cli_url", None))


def test_check_script_prefers_the_migration_credential_over_the_runtime_one(monkeypatch):
    """The runtime login cannot read catalog tables; it must never win."""
    resolved = _resolve(
        monkeypatch,
        MIGRATION_DATABASE_URL_SYNC=_MIGRATION,
        DATABASE_URL_SYNC=_RUNTIME,
    )
    assert resolved == _MIGRATION, (
        "resolved the restricted runtime login — the check would fail with "
        "'permission denied for table catalog_brands' in CI and on Fly"
    )


def test_check_script_falls_back_to_database_url_sync_when_alone(monkeypatch):
    """Local databases where both roles are the same superuser still work."""
    assert _resolve(monkeypatch, DATABASE_URL_SYNC=_RUNTIME) == _RUNTIME


def test_check_script_lets_an_explicit_url_beat_every_environment_variable(monkeypatch):
    from scripts.check_catalog_integrity import _resolve_url

    for var in _URL_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CATALOG_CHECK_DATABASE_URL", _MIGRATION)
    monkeypatch.setenv("DATABASE_URL_SYNC", _RUNTIME)
    assert _resolve_url(_EXPLICIT) == _EXPLICIT


def test_check_script_names_every_accepted_variable_when_it_has_none(monkeypatch):
    from scripts.check_catalog_integrity import _resolve_url

    for var in _URL_VARS:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SystemExit) as exc:
        _resolve_url(None)
    for var in _URL_VARS:
        assert var in str(exc.value)


# ---------------------------------------------------------------------------
# seed_catalog's own resolver must make the same choice as the check script's —
# same credential problem, same fix. It did not: after the check script was
# fixed to try MIGRATION_DATABASE_URL_SYNC first, this sibling script still
# resolved DATABASE_URL_SYNC alone, so `seed_catalog --apply` — the documented
# rollback for the damage this migration exists to prevent — could not run in
# any deployed environment. Mirror the four resolver tests so it cannot drift
# back a second time.
# ---------------------------------------------------------------------------
_SEED_URL_VARS = (
    "CATALOG_REPAIR_DATABASE_URL",
    "MIGRATION_DATABASE_URL_SYNC",
    "DATABASE_URL_SYNC",
)


def _resolve_seed(monkeypatch, **env: str) -> str:
    from scripts.seed_catalog import _resolve_url

    for var in _SEED_URL_VARS:
        monkeypatch.delenv(var, raising=False)
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    return _resolve_url(env.pop("cli_url", None))


def test_seed_script_prefers_the_migration_credential_over_the_runtime_one(monkeypatch):
    """The runtime login cannot read catalog tables; it must never win."""
    resolved = _resolve_seed(
        monkeypatch,
        MIGRATION_DATABASE_URL_SYNC=_MIGRATION,
        DATABASE_URL_SYNC=_RUNTIME,
    )
    assert resolved == _MIGRATION, (
        "resolved the restricted runtime login — the documented rollback would "
        "fail with 'permission denied for table catalog_brands' in CI and on Fly"
    )


def test_seed_script_falls_back_to_database_url_sync_when_alone(monkeypatch):
    """Local databases where both roles are the same superuser still work."""
    assert _resolve_seed(monkeypatch, DATABASE_URL_SYNC=_RUNTIME) == _RUNTIME


def test_seed_script_lets_an_explicit_url_beat_every_environment_variable(monkeypatch):
    from scripts.seed_catalog import _resolve_url

    for var in _SEED_URL_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CATALOG_REPAIR_DATABASE_URL", _MIGRATION)
    monkeypatch.setenv("DATABASE_URL_SYNC", _RUNTIME)
    assert _resolve_url(_EXPLICIT) == _EXPLICIT


def test_seed_script_names_every_accepted_variable_when_it_has_none(monkeypatch):
    from scripts.seed_catalog import _resolve_url

    for var in _SEED_URL_VARS:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SystemExit) as exc:
        _resolve_url(None)
    for var in _SEED_URL_VARS:
        assert var in str(exc.value)
