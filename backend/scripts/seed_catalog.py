"""Idempotent catalog backfill / repair (MUDD-135).

WHAT THIS USED TO BE, AND WHY IT IS NOT THAT ANY MORE
=====================================================

Until MUDD-135 this file carried a 6-category literal (``phone``, ``laptop``,
``tv``, ``appliance``, ``car``, ``furniture``) and upserted it by slug. It had
been dead for three rounds and in two independent ways, both reproduced before
this rewrite:

* It writes ``CatalogModel(suggested_price=…)``. ``r38catalogv2colors`` **dropped
  that column** ("prices change too often"), so the script dies with
  ``TypeError: 'suggested_price' is an invalid keyword argument for CatalogModel``
  before inserting a single model.
* It connects through ``app.db.SessionLocal``, i.e. the restricted runtime login
  ``muddato_api``. After ``r99globalrls``'s least-privilege pass that role has no
  rights on the catalog tables, so the very first SELECT fails with
  ``InsufficientPrivilegeError: permission denied for table catalog_categories``.
  Fixing only the column surfaces this one.

More importantly, its data was a **rival taxonomy**. The live catalog is seeded
by migrations r43–r55/r96/r97 and uses Uzbek slugs — ``smartfonlar``,
``noutbuklar``, ``televizorlar``. Had the script ever run, ``phone`` and
``smartfonlar`` would both exist as categories, brands would be split across
them, and every rollup would count one shop's phones twice. Deleting the literal
is not a loss of data: none of it was ever reachable, and all of it is
superseded by the migration-seeded catalog.

WHAT IT IS NOW
==============

The migrations own the catalog's *content*. What has never existed is something
that owns its *consistency* — a re-runnable pass that repairs the references
``rl0catintegrity`` now prevents from breaking again, for databases that were
damaged before it landed. That is this script.

It is the write half of ``scripts/check_catalog_integrity.py``: the check names
every problem, this fixes the subset that can be fixed without guessing.

    # report what would change, touch nothing (default)
    uv run python -m scripts.seed_catalog

    # actually apply it
    uv run python -m scripts.seed_catalog --apply

    # explicit target (otherwise CATALOG_REPAIR_DATABASE_URL,
    # MIGRATION_DATABASE_URL_SYNC or DATABASE_URL_SYNC, in that order)
    uv run python -m scripts.seed_catalog --apply --url postgresql+psycopg://…

Run it with the migration credential, not ``muddato_api`` — see above. The
restricted runtime login has no privileges on the catalog tables after r99,
and ``DATABASE_URL_SYNC`` is bound to exactly that login in CI and on Fly, so
(mirroring ``scripts/check_catalog_integrity.py``) ``MIGRATION_DATABASE_URL_SYNC``
is tried before it and the runtime one is kept only as a last resort for local
databases where the two roles are the same.

THE REPAIRS, AND WHERE EACH ONE STOPS
=====================================

R1  ``category_ids`` entries naming a category that does not exist  → removed.
R2  a live ``category_id`` the brand's own ``category_ids`` omits    → added.
R3  a ``category_id`` naming a category that does not exist          → cleared.
R4  a model with ``category_id IS NULL``, whose brand serves exactly
    **one** category                                                 → re-pointed.

R1–R3 restore an invariant that has exactly one correct answer, so they are safe
to apply unattended. R4 is the only inference here and it is deliberately the
narrowest one available: if the brand serves two or more categories there is no
way to tell which of them an orphaned model belonged to — an orphaned Apple
model could be an iPad or an iPhone — so the script reports it and leaves it
alone rather than filing it under a plausible guess. Inventing a link is how a
catalog stops being trustworthy.

Nothing here creates categories, brands or models. If a category has no model
line, that is a sourcing question for a human with a supplier catalogue
(``check_catalog_integrity`` reports it as a note), not something to synthesise.

IDEMPOTENT
==========

Every repair is expressed as "``UPDATE`` … ``WHERE`` <still broken>", so a
second run matches zero rows and reports zero changes. Proven in
``tests/test_mudd135_catalog_integrity.py::test_repair_is_idempotent``.

ROLLBACK
========

The script prints, and ``--json`` emits, the primary key and the before-value of
every row it changes. Reverting is therefore a per-row ``UPDATE`` back to the
recorded value; there is no bulk undo, and there should not be — R1 and R3 only
ever remove references to rows that are already gone, so "undoing" them means
re-inserting a pointer to nothing. Take a dump first if the operator wants one
(``scripts/backup_db.sh``). See ``docs/CATALOG_DEFAULTS_AND_OVERRIDES.md``
§ Rollback.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import sqlalchemy as sa


def _resolve_url(cli_url: str | None) -> str:
    # Order matters — mirrors scripts/check_catalog_integrity.py. Every deployed
    # environment sets DATABASE_URL_SYNC to the restricted runtime login, which
    # r99 stripped of all rights on the catalog tables — reaching it first makes
    # this documented rollback command fail with "permission denied for table
    # catalog_brands". The migration credential is the one that can actually
    # read and write them, so it wins.
    url = (
        cli_url
        or os.environ.get("CATALOG_REPAIR_DATABASE_URL")
        or os.environ.get("MIGRATION_DATABASE_URL_SYNC")
        or os.environ.get("DATABASE_URL_SYNC")
    )
    if not url:
        sys.exit(
            "no database URL — pass --url, or set CATALOG_REPAIR_DATABASE_URL / "
            "MIGRATION_DATABASE_URL_SYNC / DATABASE_URL_SYNC",
        )
    # asyncpg cannot drive a plain `create_engine`; this repair is synchronous.
    return url.replace("+asyncpg", "+psycopg")


# ── R1: brand.category_ids entries pointing at a category that is not there ──

_R1_FIND = sa.text("""
    SELECT b.id::text AS id, b.slug, b.category_ids::text AS before
      FROM catalog_brands b
     WHERE EXISTS (SELECT 1 FROM jsonb_array_elements_text(b.category_ids) AS x(cid)
                    WHERE NOT EXISTS (SELECT 1 FROM catalog_categories c WHERE c.id::text = x.cid))
     ORDER BY b.slug
""")

_R1_FIX = sa.text("""
    UPDATE catalog_brands b
       SET category_ids = COALESCE((
             SELECT jsonb_agg(x.cid)
               FROM jsonb_array_elements_text(b.category_ids) AS x(cid)
              WHERE EXISTS (SELECT 1 FROM catalog_categories c WHERE c.id::text = x.cid)
           ), '[]'::jsonb)
     WHERE EXISTS (SELECT 1 FROM jsonb_array_elements_text(b.category_ids) AS y(cid)
                    WHERE NOT EXISTS (SELECT 1 FROM catalog_categories c WHERE c.id::text = y.cid))
""")

# ── R2: a live primary category the brand's own category_ids omits ───────────

_R2_FIND = sa.text("""
    SELECT b.id::text AS id, b.slug, b.category_ids::text AS before
      FROM catalog_brands b
     WHERE b.category_id IS NOT NULL
       AND NOT (b.category_ids ? b.category_id::text)
       AND EXISTS (SELECT 1 FROM catalog_categories c WHERE c.id = b.category_id)
     ORDER BY b.slug
""")

_R2_FIX = sa.text("""
    UPDATE catalog_brands b
       SET category_ids = b.category_ids || to_jsonb(b.category_id::text)
     WHERE b.category_id IS NOT NULL
       AND NOT (b.category_ids ? b.category_id::text)
       AND EXISTS (SELECT 1 FROM catalog_categories c WHERE c.id = b.category_id)
""")

# ── R3: a primary category that no longer exists ─────────────────────────────

_R3_FIND = sa.text("""
    SELECT b.id::text AS id, b.slug, b.category_id::text AS before
      FROM catalog_brands b
     WHERE b.category_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM catalog_categories c WHERE c.id = b.category_id)
     ORDER BY b.slug
""")

_R3_FIX = sa.text("""
    UPDATE catalog_brands b SET category_id = NULL
     WHERE b.category_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM catalog_categories c WHERE c.id = b.category_id)
""")

# ── R4: an orphaned model whose brand serves exactly one category ────────────
#
# `_single_category_brands` is the whole judgement: a brand with one category has
# one possible answer, a brand with two has none this script may pick.

_R4_FIND = sa.text("""
    WITH one_category AS (
        SELECT b.id AS brand_id,
               (SELECT x.cid FROM jsonb_array_elements_text(b.category_ids) AS x(cid) LIMIT 1)::uuid AS cid
          FROM catalog_brands b
         WHERE jsonb_array_length(b.category_ids) = 1
    )
    SELECT m.id::text AS id, m.slug, b.slug AS brand,
           o.cid::text AS after, c.slug AS after_slug
      FROM catalog_models m
      JOIN catalog_brands b ON b.id = m.brand_id
      JOIN one_category o ON o.brand_id = m.brand_id
      JOIN catalog_categories c ON c.id = o.cid
     WHERE m.category_id IS NULL
     ORDER BY b.slug, m.slug
""")

_R4_FIX = sa.text("""
    UPDATE catalog_models m
       SET category_id = (
             SELECT x.cid::uuid FROM catalog_brands b,
                    LATERAL jsonb_array_elements_text(b.category_ids) AS x(cid)
              WHERE b.id = m.brand_id AND jsonb_array_length(b.category_ids) = 1
              LIMIT 1)
     WHERE m.category_id IS NULL
       AND EXISTS (SELECT 1 FROM catalog_brands b
                    WHERE b.id = m.brand_id AND jsonb_array_length(b.category_ids) = 1)
""")

#: What R4 refuses to guess — reported, never changed.
_R4_AMBIGUOUS = sa.text("""
    SELECT m.id::text AS id, m.slug, b.slug AS brand,
           jsonb_array_length(b.category_ids) AS candidate_categories
      FROM catalog_models m JOIN catalog_brands b ON b.id = m.brand_id
     WHERE m.category_id IS NULL AND jsonb_array_length(b.category_ids) <> 1
     ORDER BY b.slug, m.slug
""")

REPAIRS: tuple[tuple[str, str, sa.TextClause, sa.TextClause], ...] = (
    ("R1", "brand category_ids naming a category that is gone", _R1_FIND, _R1_FIX),
    ("R2", "brand primary category missing from its own category_ids", _R2_FIND, _R2_FIX),
    ("R3", "brand primary category naming a category that is gone", _R3_FIND, _R3_FIX),
    ("R4", "orphaned model re-pointed at its brand's only category", _R4_FIND, _R4_FIX),
)


def run(conn: sa.Connection, *, apply: bool) -> dict:
    """Find (and optionally apply) every repair. Returns the full report.

    With ``apply=False`` nothing is written — the caller may still be inside a
    read-only transaction.
    """
    report: dict = {"applied": apply, "repairs": [], "ambiguous": [], "changed": 0}

    for code, description, find, fix in REPAIRS:
        rows = [dict(r._mapping) for r in conn.execute(find)]
        changed = 0
        if apply and rows:
            changed = conn.execute(fix).rowcount
        report["repairs"].append({
            "code": code, "description": description,
            "found": len(rows), "changed": changed, "rows": rows,
        })
        report["changed"] += changed

    report["ambiguous"] = [dict(r._mapping) for r in conn.execute(_R4_AMBIGUOUS)]
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Idempotent catalog repair (MUDD-135).")
    ap.add_argument("--url", default=None)
    ap.add_argument("--apply", action="store_true",
                    help="write the repairs (default: report only, touch nothing)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--show", type=int, default=20,
                    help="how many affected rows to print per repair (default 20)")
    args = ap.parse_args()

    engine = sa.create_engine(_resolve_url(args.url))
    if args.apply:
        with engine.begin() as conn:
            report = run(conn, apply=True)
    else:
        with engine.connect() as conn:
            conn.execute(sa.text("SET TRANSACTION READ ONLY"))
            report = run(conn, apply=False)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return 0

    print("MODE:", "APPLY (writing)" if args.apply else "DRY RUN (nothing written)")
    for r in report["repairs"]:
        print(f"\n{r['code']}  {r['description']}")
        print(f"    found={r['found']}  changed={r['changed']}")
        for row in r["rows"][: args.show]:
            print("      " + ", ".join(f"{k}={v}" for k, v in row.items()))
        if r["found"] > args.show:
            print(f"      … and {r['found'] - args.show} more")

    if report["ambiguous"]:
        print(f"\nNOT REPAIRED — needs a human ({len(report['ambiguous'])}):")
        print("  an orphaned model whose brand serves several categories has no")
        print("  single correct answer; pick one in the platform catalog UI.")
        for row in report["ambiguous"][: args.show]:
            print("      " + ", ".join(f"{k}={v}" for k, v in row.items()))

    print(f"\ntotal rows changed: {report['changed']}")
    if not args.apply and any(r["found"] for r in report["repairs"]):
        print("re-run with --apply to write these.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
