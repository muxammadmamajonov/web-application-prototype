"""READ-ONLY: missing / duplicate / orphaned rows in the global catalog (MUDD-135).

The catalog is three tables deep (``catalog_categories`` → ``catalog_brands`` →
``catalog_models``) plus one tenant-side reference (``products.catalog_model_id``),
and the link that matters most — a brand's ``category_ids`` — is a JSONB array,
so **Postgres cannot hold it up with a foreign key**. Everything the database
would normally guarantee about that edge has to be asked for.

Same posture as ``scripts/audit_r96_required_identifiers.py``: one connection,
``SET TRANSACTION READ ONLY``, no DDL, no writes, and the schema rules come from
``app.core.catalog_schema`` rather than being reimplemented in SQL so this and
the API cannot drift on what "well-formed" means.

Usage::

    uv run python -m scripts.check_catalog_integrity \\
        --url postgresql+psycopg://user:pass@host:5432/db [--json]

The URL may also come from ``CATALOG_CHECK_DATABASE_URL``,
``MIGRATION_DATABASE_URL_SYNC`` or ``DATABASE_URL_SYNC``, in that order.
The restricted runtime login (``muddato_api``) has no privileges on the catalog
tables after r99, and ``DATABASE_URL_SYNC`` is bound to exactly that login in CI
and on Fly — so the migration credential is tried first and the runtime one is
kept only as a last resort for local databases where the two are the same role.

**Errors versus notes.** Findings are split into two buckets and only the first
decides the exit code:

* ``errors``  — a broken reference or a duplicate. Something points at a row
  that is not there, or two rows claim one identity. These are always wrong.
* ``notes``   — completeness observations. "This category has no models" is the
  largest one (108 of 114 categories today) and it is deliberately NOT an
  error: r97catmodels seeded model lines only for the six categories where a
  named model is unambiguous, and inventing the rest would put fabricated
  product names in front of real customers. A note is a question for a human
  with a supplier catalogue, not a failure. A model under a deactivated brand
  or category is also a note, not an error — deactivation is the supported
  retirement path for content that RESTRICT now refuses to ``DELETE``, so
  every such model becoming unreachable is that path's intended effect.

Exit code is ``1`` when there is at least one error, ``0`` otherwise, so this
runs as a deploy gate as well as by hand.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterator

import sqlalchemy as sa

from app.core.catalog_schema import validate_attribute_schema, validate_identifier_schema


def _resolve_url(cli_url: str | None) -> str:
    # Order matters. Every deployed environment sets DATABASE_URL_SYNC to the
    # restricted runtime login, which r99 stripped of all rights on the catalog
    # tables — reaching it first makes the documented operator command fail with
    # "permission denied for table catalog_brands". The migration credential is
    # the one that can actually read them, so it wins.
    url = (
        cli_url
        or os.environ.get("CATALOG_CHECK_DATABASE_URL")
        or os.environ.get("MIGRATION_DATABASE_URL_SYNC")
        or os.environ.get("DATABASE_URL_SYNC")
    )
    if not url:
        sys.exit(
            "no database URL — pass --url, or set CATALOG_CHECK_DATABASE_URL / "
            "MIGRATION_DATABASE_URL_SYNC / DATABASE_URL_SYNC",
        )
    # asyncpg cannot drive a plain `create_engine`; this check is synchronous.
    return url.replace("+asyncpg", "+psycopg")


def _rows(conn: sa.Connection, sql: str) -> list[sa.Row]:
    return list(conn.execute(sa.text(sql)).all())


def _json(raw: object) -> object:
    """`identifier_schema` / `attribute_schema` are stored as `json`, not `jsonb`
    (r32catalogschema), so psycopg may hand back either a parsed object or the
    raw text depending on the driver. Accept both."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return None
    return raw


# ── errors: something points at nothing, or two rows claim one identity ──────

def _dangling_brand_categories(conn: sa.Connection) -> Iterator[dict]:
    """A brand's ``category_ids`` naming a category that is not there.

    No FK can exist on a JSONB array, and ``DELETE /catalog/categories/{id}``
    does not sweep the arrays, so every category deletion leaves this behind.
    """
    for row in _rows(conn, """
        SELECT b.slug AS brand_slug, x.cid
          FROM catalog_brands b,
               LATERAL jsonb_array_elements_text(b.category_ids::jsonb) AS x(cid)
         WHERE NOT EXISTS (SELECT 1 FROM catalog_categories c WHERE c.id::text = x.cid)
         ORDER BY b.slug
    """):
        yield {"check": "brand_category_ids_dangling",
               "brand": row.brand_slug, "category_id": row.cid}


def _brand_primary_outside_category_ids(conn: sa.Connection) -> Iterator[dict]:
    """``category_id`` (the legacy "primary") not listed in ``category_ids``.

    ``category_ids`` is the source of truth (``app/models/catalog.py``), but
    ``uq_brand_category_slug`` is enforced on ``category_id`` — so when the two
    disagree, uniqueness is being enforced against a category the brand does
    not claim to serve.
    """
    for row in _rows(conn, """
        SELECT slug FROM catalog_brands
         WHERE category_id IS NOT NULL
           AND NOT (category_ids::jsonb ? category_id::text)
         ORDER BY slug
    """):
        yield {"check": "brand_primary_not_in_category_ids", "brand": row.slug}


def _brands_without_categories(conn: sa.Connection) -> Iterator[dict]:
    """A brand nothing can reach: empty ``category_ids`` and no ``category_id``.
    Both catalog read endpoints filter on one or the other, so such a row is
    invisible in every picker while still occupying its slug."""
    for row in _rows(conn, """
        SELECT slug FROM catalog_brands
         WHERE (category_ids IS NULL OR jsonb_array_length(category_ids::jsonb) = 0)
           AND category_id IS NULL
         ORDER BY slug
    """):
        yield {"check": "brand_without_categories", "brand": row.slug}


def _duplicate_brand_slugs(conn: sa.Connection) -> Iterator[dict]:
    """The same brand as two rows.

    ``uq_brand_category_slug`` is ``(category_id, slug)``, so "Apple" may legally
    exist once per primary category — which is the pre-``category_ids`` shape and
    splits one brand's models across rows that no query joins back together.
    """
    for row in _rows(conn, """
        SELECT slug, count(*) AS n FROM catalog_brands
         GROUP BY slug HAVING count(*) > 1 ORDER BY slug
    """):
        yield {"check": "brand_duplicate_slug", "brand": row.slug, "rows": int(row.n)}


def _models_without_category(conn: sa.Connection) -> Iterator[dict]:
    """A model whose ``category_id`` is NULL.

    The FK is ``ON DELETE SET NULL``, so deleting a category silently empties it.
    Such a model resolves no ``identifier_schema`` and is filtered out of every
    category-scoped picker — present in the table, absent from the product.
    """
    for row in _rows(conn, """
        SELECT m.slug, b.slug AS brand_slug
          FROM catalog_models m JOIN catalog_brands b ON b.id = m.brand_id
         WHERE m.category_id IS NULL ORDER BY b.slug, m.slug
    """):
        yield {"check": "model_without_category",
               "model": row.slug, "brand": row.brand_slug}


def _models_in_category_their_brand_does_not_serve(conn: sa.Connection) -> Iterator[dict]:
    """``POST /catalog/models`` takes ``brand_id`` and ``category_id`` and checks
    neither against the other, so a model can be filed under a category its own
    brand does not serve. ``GET /categories/{slug}/brands`` then counts it for a
    brand that will never be offered there."""
    for row in _rows(conn, """
        SELECT m.slug, b.slug AS brand_slug, c.slug AS category_slug
          FROM catalog_models m
          JOIN catalog_brands b ON b.id = m.brand_id
          JOIN catalog_categories c ON c.id = m.category_id
         WHERE NOT (b.category_ids::jsonb ? m.category_id::text)
           AND b.category_id IS DISTINCT FROM m.category_id
         ORDER BY b.slug, m.slug
    """):
        yield {"check": "model_category_not_served_by_brand", "model": row.slug,
               "brand": row.brand_slug, "category": row.category_slug}


def _models_under_a_dead_parent(conn: sa.Connection) -> Iterator[dict]:
    """A live model hanging off a deactivated brand or a deactivated category.

    Deactivation (``is_active = false``) is the supported retirement path — the
    RESTRICT FKs above refuse ``DELETE`` on a category or brand that still has
    content precisely so this is used instead — and every model under it
    becoming unreachable by browsing is that path's intended effect, not a
    broken reference. Reported as a note, not an error: classifying it as an
    error would make the first legitimate retirement red the deploy gate with
    no automated remedy, since re-pointing or reviving those models is an
    editorial call for a human, not something this script may infer.
    """
    for row in _rows(conn, """
        SELECT m.slug, b.slug AS brand_slug,
               NOT b.is_active AS brand_dead,
               (c.id IS NOT NULL AND NOT c.is_active) AS category_dead
          FROM catalog_models m
          JOIN catalog_brands b ON b.id = m.brand_id
          LEFT JOIN catalog_categories c ON c.id = m.category_id
         WHERE m.is_active AND (NOT b.is_active OR (c.id IS NOT NULL AND NOT c.is_active))
         ORDER BY b.slug, m.slug
    """):
        yield {"check": "model_under_inactive_parent", "model": row.slug,
               "brand": row.brand_slug,
               "reason": "brand_inactive" if row.brand_dead else "category_inactive"}


def _products_pointing_at_no_model(conn: sa.Connection) -> Iterator[dict]:
    """``products.catalog_model_id`` naming a model that is gone.

    The FK is ``ON DELETE SET NULL`` so this should be structurally impossible;
    it is checked anyway because that is the assumption the analytics rollup
    rests on, and an assumption nobody tests is the one that turns out to be
    false. A hit here means the FK is missing on that deployment.
    """
    for row in _rows(conn, """
        SELECT p.id::text AS product_id, p.tenant_id::text AS tenant_id
          FROM products p
         WHERE p.catalog_model_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM catalog_models m WHERE m.id = p.catalog_model_id)
    """):
        yield {"check": "product_catalog_model_missing",
               "product_id": row.product_id, "tenant_id": row.tenant_id}


def _malformed_category_schemas(conn: sa.Connection) -> Iterator[dict]:
    """A category whose declared schema the readers will silently drop.

    ``CategoryIn`` types both columns as a bare ``list`` — no shape validation
    on write — and ``parse_identifier_schema`` drops malformed entries at read
    time without a word. A category can therefore display a required IMEI field
    to the platform admin and enforce nothing at the register.
    """
    for row in _rows(conn, "SELECT slug, attribute_schema, identifier_schema FROM catalog_categories ORDER BY slug"):
        problems = (
            validate_attribute_schema(_json(row.attribute_schema))
            + validate_identifier_schema(_json(row.identifier_schema))
        )
        if problems:
            yield {"check": "category_schema_malformed", "category": row.slug,
                   "problems": [str(p) for p in problems]}


ERROR_CHECKS = (
    _dangling_brand_categories,
    _brand_primary_outside_category_ids,
    _brands_without_categories,
    _duplicate_brand_slugs,
    _models_without_category,
    _models_in_category_their_brand_does_not_serve,
    _products_pointing_at_no_model,
    _malformed_category_schemas,
)


# ── notes: complete-enough questions for a human, never a build failure ──────

def _categories_without_brands(conn: sa.Connection) -> Iterator[dict]:
    for row in _rows(conn, """
        SELECT c.slug FROM catalog_categories c
         WHERE c.is_active
           AND NOT EXISTS (SELECT 1 FROM catalog_brands b
                            WHERE b.category_ids::jsonb ? c.id::text OR b.category_id = c.id)
         ORDER BY c.slug
    """):
        yield {"check": "category_without_brands", "category": row.slug}


def _categories_without_models(conn: sa.Connection) -> Iterator[dict]:
    """Not an error — see the module docstring. Reported with the brand count so
    the categories worth sourcing a model list for sort to the top."""
    for row in _rows(conn, """
        SELECT c.slug,
               (SELECT count(*) FROM catalog_brands b
                 WHERE b.category_ids::jsonb ? c.id::text OR b.category_id = c.id) AS brands
          FROM catalog_categories c
         WHERE c.is_active
           AND NOT EXISTS (SELECT 1 FROM catalog_models m WHERE m.category_id = c.id)
         ORDER BY brands DESC, c.slug
    """):
        yield {"check": "category_without_models",
               "category": row.slug, "brands": int(row.brands)}


def _categories_without_a_variant_axis(conn: sa.Connection) -> Iterator[dict]:
    """A category no model can express a SKU under: nothing in its
    ``attribute_schema`` is marked ``variant``, so every unit looks identical."""
    for row in _rows(conn, """
        SELECT c.slug FROM catalog_categories c
         WHERE c.is_active
           AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(c.attribute_schema::jsonb) e
                            WHERE (e->>'variant')::bool)
         ORDER BY c.slug
    """):
        yield {"check": "category_without_variant_axis", "category": row.slug}


def _categories_without_identifiers(conn: sa.Connection) -> Iterator[dict]:
    for row in _rows(conn, """
        SELECT c.slug FROM catalog_categories c
         WHERE c.is_active AND jsonb_array_length(c.identifier_schema::jsonb) = 0
         ORDER BY c.slug
    """):
        yield {"check": "category_without_identifier_schema", "category": row.slug}


def _products_on_legacy_free_text(conn: sa.Connection) -> Iterator[dict]:
    """A tenant product still described by the pre-catalog free-text
    ``category``/``brand`` columns with no ``catalog_model_id``. Every one of
    these is a row the cross-tenant rollup cannot see."""
    for row in _rows(conn, """
        SELECT count(*) AS n FROM products
         WHERE catalog_model_id IS NULL AND (category IS NOT NULL OR brand IS NOT NULL)
    """):
        if int(row.n):
            yield {"check": "product_without_catalog_link", "products": int(row.n)}


NOTE_CHECKS = (
    _categories_without_brands,
    _categories_without_models,
    _categories_without_a_variant_axis,
    _categories_without_identifiers,
    _products_on_legacy_free_text,
    _models_under_a_dead_parent,
)


def collect(conn: sa.Connection) -> dict:
    """Run every check and return the full report. Pure read; safe on prod."""
    errors = [finding for check in ERROR_CHECKS for finding in check(conn)]
    notes = [finding for check in NOTE_CHECKS for finding in check(conn)]
    counts = {
        name: int(conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())
        for name, table in (
            ("categories", "catalog_categories"), ("brands", "catalog_brands"),
            ("models", "catalog_models"), ("products", "products"),
        )
    }
    return {
        "counts": counts,
        "errors": errors,
        "notes": notes,
        "error_count": len(errors),
        "note_count": len(notes),
    }


def _summarise(findings: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in findings:
        out[f["check"]] = out.get(f["check"], 0) + 1
    return dict(sorted(out.items()))


def main() -> int:
    ap = argparse.ArgumentParser(description="Catalog integrity check (read-only).")
    ap.add_argument("--url", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--show", type=int, default=10,
                    help="how many individual findings to print per check (default 10)")
    args = ap.parse_args()

    engine = sa.create_engine(_resolve_url(args.url))
    with engine.connect() as conn:
        # Belt and braces: even a typo'd query cannot write from here.
        conn.execute(sa.text("SET TRANSACTION READ ONLY"))
        report = collect(conn)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1 if report["errors"] else 0

    c = report["counts"]
    print(f"categories={c['categories']}  brands={c['brands']}  "
          f"models={c['models']}  products={c['products']}")

    for label, findings in (("ERRORS", report["errors"]), ("NOTES", report["notes"])):
        print(f"\n{label}: {len(findings)}")
        for check, n in _summarise(findings).items():
            print(f"  {check:<40} {n}")
            shown = [f for f in findings if f["check"] == check][: args.show]
            for f in shown:
                detail = ", ".join(f"{k}={v}" for k, v in f.items() if k != "check")
                print(f"      {detail}")
            if n > len(shown):
                print(f"      … and {n - len(shown)} more")

    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
