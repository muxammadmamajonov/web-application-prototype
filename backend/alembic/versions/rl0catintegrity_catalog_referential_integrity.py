"""Make the catalog's own references hold (MUDD-135).

Revision ID: rl0catintegrity
Revises: r99anlytidx
Create Date: 2026-09-05

The catalog is clean today because every row in it was written by a migration.
Nothing keeps it that way: the write endpoints in `app/api/catalog.py` can break
each of the invariants below, and the database helps them do it.

MEASURED, not assumed — against a freshly migrated database (114 categories,
921 brands, 125 models), one `DELETE FROM catalog_categories WHERE
slug='planshetlar'` — exactly what `DELETE /catalog/categories/{id}` issues —
left:

  * 921 → 916 brands. Five brands were CASCADE-deleted because tablets happened
    to be their `category_id`, even though `category_ids` said they also serve
    phones, laptops and watches. Apple survives that particular delete but not
    the equivalent one on `smartfonlar`: deleting phones takes the Apple row and
    all 10 of its models with it, out of every one of the 7 categories it serves.
  * 6 surviving brands still carrying the deleted category's UUID in
    `category_ids` — no FK is possible on a JSONB array, so nothing swept them.
  * 8 models silently reset to `category_id = NULL` by the `SET NULL` FK. A model
    with no category resolves no `identifier_schema` and is filtered out of every
    category-scoped picker: present in the table, gone from the product.

None of it raises, and none of it is logged.

WHAT THIS CHANGES

1. `catalog_brands.category_id`: `ON DELETE CASCADE` → `RESTRICT`.
2. `catalog_models.category_id`: `ON DELETE SET NULL` → `RESTRICT`.

   Both turn a silent deletion into a loud refusal. A category that still has
   brands or models can no longer be deleted at all — it has to be deactivated
   (`is_active = false`), which is the mechanism the catalog already has and the
   one the read endpoints already honour. Deleting a category nothing references
   still works, so the "I mistyped a category, remove it" path is untouched.

3. `uq_catalog_brands_slug` — a plain UNIQUE on `catalog_brands.slug`.

   `category_ids` is the source of truth (`app/models/catalog.py`) but
   `uq_brand_category_slug` is enforced on the legacy `category_id`, so "Apple"
   may legally exist as several rows, one per primary category — the shape the
   catalog had before `category_ids`, in which one brand's models are split
   across rows that no query joins back together. The data has 921 brands and
   921 distinct slugs, so this constraint describes what is already true and
   stops it drifting. `uq_brand_category_slug` is left in place: it is now
   implied, and dropping a constraint is a wider change than this needs.

`products.catalog_model_id` is deliberately NOT changed. It is `ON DELETE SET
NULL`, which cannot dangle, and a tenant's product must outlive the catalog row
it was matched to — losing the cross-tenant rollup link is the correct, lossless
outcome there, not a reason to block the platform from retiring a model.

IDEMPOTENT. The heal in step 0 is written as `UPDATE … WHERE <still broken>`, so
a second run matches no rows; the constraint work is guarded on
`pg_constraint`/`pg_indexes`, so a second run creates nothing. Re-running
`upgrade()` against an already-upgraded database is a no-op (proven in
`tests/test_mudd135_catalog_integrity.py::test_migration_upgrade_is_idempotent`).

ROLLBACK. `downgrade()` restores both FK actions and drops the unique index,
returning the schema exactly to r98healtpl. It does NOT undo the step-0 heal —
re-inserting a UUID that points at a category that does not exist would be
restoring corruption, and the heal only ever removed references that were
already dangling. See `docs/CATALOG_DEFAULTS_AND_OVERRIDES.md` § Rollback.

FAILURE MODE TO EXPECT ON A DIRTY DATABASE. If a deployment already has a model
whose `category_id` is NULL, or a brand whose `category_id` points at a category
that no longer exists, the `RESTRICT` FKs still apply cleanly (they constrain
future deletes, not existing rows) — but `uq_catalog_brands_slug` will refuse to
build if duplicate brand slugs exist. Run
`python -m scripts.check_catalog_integrity` first; it names every offending row.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "rl0catintegrity"
down_revision = "r99anlytidx"
branch_labels = None
depends_on = None

_BRAND_FK = "fk_catalog_brands_category_id_catalog_categories"
_MODEL_FK = "fk_catalog_models_category_id_catalog_categories"
_BRAND_SLUG_UQ = "uq_catalog_brands_slug"


def _constraint_exists(conn: sa.Connection, table: str, name: str) -> bool:
    return bool(conn.execute(sa.text(
        "SELECT 1 FROM pg_constraint WHERE conname = :n AND conrelid = to_regclass(:t)"
    ), {"n": name, "t": table}).scalar())


def _readd_fk(table: str, name: str, ondelete: str) -> None:
    op.drop_constraint(name, table, type_="foreignkey")
    op.create_foreign_key(
        name, table, "catalog_categories", ["category_id"], ["id"], ondelete=ondelete,
    )


def upgrade() -> None:
    conn = op.get_bind()

    # ── step 0: heal what is already broken, so the constraints can be trusted ──
    # Drop `category_ids` entries naming a category that is not there. Rebuilding
    # the array with a filtered aggregate rather than a JSONB delete operator
    # keeps the result a well-formed array even when every entry is dangling.
    healed_ids = conn.execute(sa.text("""
        UPDATE catalog_brands b
           SET category_ids = COALESCE((
                 SELECT jsonb_agg(x.cid)
                   FROM jsonb_array_elements_text(b.category_ids) AS x(cid)
                  WHERE EXISTS (SELECT 1 FROM catalog_categories c WHERE c.id::text = x.cid)
               ), '[]'::jsonb)
         WHERE EXISTS (
                 SELECT 1 FROM jsonb_array_elements_text(b.category_ids) AS y(cid)
                  WHERE NOT EXISTS (SELECT 1 FROM catalog_categories c WHERE c.id::text = y.cid)
               )
    """)).rowcount

    # Re-unify the legacy/primary split the other way: a live `category_id` that
    # the brand's own `category_ids` does not list. `category_ids` is the source
    # of truth, so the primary joins it rather than replacing it.
    healed_primary = conn.execute(sa.text("""
        UPDATE catalog_brands b
           SET category_ids = b.category_ids || to_jsonb(b.category_id::text)
         WHERE b.category_id IS NOT NULL
           AND NOT (b.category_ids ? b.category_id::text)
           AND EXISTS (SELECT 1 FROM catalog_categories c WHERE c.id = b.category_id)
    """)).rowcount

    # A brand whose primary points at a category that no longer exists: clear the
    # pointer instead of deleting the brand, then let `category_ids` (already
    # healed above) carry it. Without this the RESTRICT FK below cannot be built.
    cleared_primary = conn.execute(sa.text("""
        UPDATE catalog_brands b SET category_id = NULL
         WHERE b.category_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM catalog_categories c WHERE c.id = b.category_id)
    """)).rowcount

    print(f"[rl0catintegrity] healed brands: {healed_ids} with dangling category_ids, "
          f"{healed_primary} with an unlisted primary, {cleared_primary} with a dead primary")

    # ── step 1+2: stop the silent deletes ──────────────────────────────────────
    if _constraint_exists(conn, "catalog_brands", _BRAND_FK):
        _readd_fk("catalog_brands", _BRAND_FK, "RESTRICT")
    if _constraint_exists(conn, "catalog_models", _MODEL_FK):
        _readd_fk("catalog_models", _MODEL_FK, "RESTRICT")

    # ── step 3: one row per brand ──────────────────────────────────────────────
    if not _constraint_exists(conn, "catalog_brands", _BRAND_SLUG_UQ):
        dupes = conn.execute(sa.text(
            "SELECT slug, count(*) FROM catalog_brands GROUP BY slug HAVING count(*) > 1"
        )).all()
        if dupes:
            raise RuntimeError(
                "cannot add uq_catalog_brands_slug — duplicate brand slugs exist: "
                + ", ".join(f"{slug} x{n}" for slug, n in dupes)
                + ". Run `python -m scripts.check_catalog_integrity` and merge them first."
            )
        op.create_unique_constraint(_BRAND_SLUG_UQ, "catalog_brands", ["slug"])

    print("[rl0catintegrity] catalog category deletes now RESTRICT; brand slug is unique")


def downgrade() -> None:
    conn = op.get_bind()
    if _constraint_exists(conn, "catalog_brands", _BRAND_SLUG_UQ):
        op.drop_constraint(_BRAND_SLUG_UQ, "catalog_brands", type_="unique")
    if _constraint_exists(conn, "catalog_models", _MODEL_FK):
        _readd_fk("catalog_models", _MODEL_FK, "SET NULL")
    if _constraint_exists(conn, "catalog_brands", _BRAND_FK):
        _readd_fk("catalog_brands", _BRAND_FK, "CASCADE")
    # The step-0 heal is not reversed on purpose — see the module docstring.
