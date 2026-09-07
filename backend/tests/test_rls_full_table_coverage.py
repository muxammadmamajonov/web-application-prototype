"""Every table must have a decided RLS posture — covered, or explicitly exempt.

TWO PRODUCTION OUTAGES, ONE SHAPE (both 2026-08-06):

  1. Creating a contract died with `new row violates row-level security policy
     for table "installment_items"` (fixed by r94childrls).
  2. A platform receiving card was saved, and every tenant's subscription page
     still said "no receiving card yet" with the top-up button disabled. The
     row was there; `platform_settings` was invisible (fixed by r95globalrls).

Neither table was a mistake in any migration. They were tables that **no
migration had an opinion about**. `r73` scopes tables carrying a `tenant_id`
column and correctly skips the rest; the live database nonetheless has RLS
switched on everywhere (Supabase default), and RLS enabled with no policy
denies everything to a non-superuser. So "not mentioned anywhere" silently
means "locked out in production, works fine everywhere else".

That is why this test exists, and why it is a COVERAGE test rather than a
behavioural one. A behavioural test cannot catch this: the test database is
built with `create_all`, never runs migrations, and runs with RLS off — the
exact blind spot that let both outages through. What CAN be checked mechanically
is that no table is unaccounted for. Adding a table now forces a decision:
police it, or name it here and say why.

The two verbs fail differently, which is worth remembering when triaging:
INSERT raises InsufficientPrivilegeError (loud, found in an hour); SELECT
returns zero rows (silent, survived three fix attempts).
"""
from __future__ import annotations

import importlib.util
import pathlib
import re

VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"

#: Tables with no RLS policy ON PURPOSE. Only the scheduler and Alembic touch
#: these, and both run as `postgres`, which bypasses RLS — so leaving them
#: unreachable from a tenant request is a hardening, not an oversight. A tenant
#: path that ever needs one will fail LOUDLY on write, which is the outcome we
#: want for infrastructure tables.
DELIBERATELY_UNPOLICED: frozenset[str] = frozenset({"cron_locks"})


def _mapped_tables() -> set[str]:
    import importlib
    import pkgutil

    import app.models as models

    for m in pkgutil.iter_modules(models.__path__):
        importlib.import_module(f"app.models.{m.name}")
    from app.db import Base

    return set(Base.metadata.tables)


def _load(path: pathlib.Path):
    """`alembic/versions` is not an importable package, so load by path."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _policed_tables() -> set[str]:
    """Union of every table any migration enables RLS on.

    Two forms are in use across the migrations and both must be read:
    a literal ``ALTER TABLE products ENABLE …`` and a loop over a module-level
    tuple of names (r73's TENANT_TABLES, r94's CHILD_TABLES, r95's lists). The
    literal form is scraped from the source; the loop form is read off the
    imported module, since an f-string reveals nothing statically.
    """
    found: set[str] = set()
    for path in sorted(VERSIONS.glob("*.py")):
        text = path.read_text(encoding="utf8")
        if "ROW LEVEL SECURITY" not in text:
            continue
        found |= set(re.findall(r"ALTER TABLE (\w+) ENABLE ROW LEVEL SECURITY", text))
        for value in vars(_load(path)).values():
            if isinstance(value, (tuple, list)) and value:
                if all(isinstance(v, str) for v in value):
                    found |= set(value)
                # r94's CHILD_TABLES is a tuple of (table, fk) pairs.
                elif all(isinstance(v, tuple) and v and isinstance(v[0], str) for v in value):
                    found |= {v[0] for v in value}
    return found


def test_every_table_has_a_decided_rls_posture():
    undecided = sorted(_mapped_tables() - _policed_tables() - DELIBERATELY_UNPOLICED)
    assert not undecided, (
        "these tables have no RLS policy in any migration and are not listed as "
        f"deliberately unpoliced: {undecided}\n\n"
        "In production RLS is ON for every table (Supabase default), and RLS with "
        "no policy denies everything to the restricted `muddato_app` role. A tenant "
        "request will therefore either be REFUSED on write or — far worse — read "
        "back ZERO ROWS with no error at all, which reads as 'nothing configured'.\n"
        "Add a policy in a migration, or add the table to DELIBERATELY_UNPOLICED "
        "with a note explaining why the tenant path never touches it."
    )


def test_the_two_tables_that_caused_outages_stay_covered():
    policed = _policed_tables()
    # r95globalrls — the invisible receiving card.
    assert "platform_settings" in policed
    assert "tenants" in policed, "a tenant could not read its own row"
    # r94childrls — the failed contract insert.
    assert "installment_items" in policed


def test_global_reference_data_is_readable_but_not_tenant_writable():
    """The global tables must be READ-open and WRITE-closed to tenants.

    Getting this backwards is how the outage happened, and blanket-opening them
    would be the wrong overcorrection: a tenant request must not be able to
    rewrite the platform's own receiving cards or the shared catalogue.
    """
    mod = _load(VERSIONS / "r95globalrls_global_table_rls.py")
    assert "platform_settings" in mod.GLOBAL_READ_ONLY
    assert "plans" in mod.GLOBAL_READ_ONLY

    src = (VERSIONS / "r95globalrls_global_table_rls.py").read_text(encoding="utf8")
    # Reads open to everyone …
    assert "USING (true) WITH CHECK ({_NO_SCOPE})" in src or "USING (true)" in src
    # … writes gated on there being no tenant scope, i.e. the platform path.
    assert "current_setting('app.tenant_id', true), '') IS NULL" in mod._NO_SCOPE
