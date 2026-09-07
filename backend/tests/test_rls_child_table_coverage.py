"""Every child of `installments` must be named in the child-RLS migration.

THE OUTAGE THIS PINS (production, 2026-08-06). Creating a contract failed with

    new row violates row-level security policy for table "installment_items"

`r73_rls_tenant_isolation` protects every table that CARRIES a `tenant_id`
column. `installment_items` and `installment_guarantors` do not carry one — they
inherit tenancy from their parent — so r73 skipped them, correctly. What nobody
accounted for is that the live database had RLS switched on for those tables
anyway (Supabase does that by default), and **RLS enabled with no policy denies
everything** to a non-superuser. The tenant path is the only writer, so the core
workflow broke while every platform path — running as `postgres`, which bypasses
RLS — kept working. That asymmetry is why it reached production.

`r94childrls` adds a policy that reaches tenancy through the parent. This test
makes the coverage rule mechanical: if someone adds a third child table of
`installments`, it must be listed there too, or this fails.

It is a MODEL-level check, not a database one, because the test database is
built with `create_all` and never runs migrations, so an RLS behavioural test
would pass here whatever the policies say — exactly the blind spot that let the
outage through in the first place. The behaviour itself was verified by hand
against a migrated database: an insert onto the caller's own installment is
allowed, one onto another tenant's is refused.
"""
from __future__ import annotations

import importlib
import pkgutil


def _all_mapped_tables() -> dict[str, object]:
    """Import every model module so the registry is complete, then map the
    declarative tables by name."""
    import app.models as models

    for m in pkgutil.iter_modules(models.__path__):
        importlib.import_module(f"app.models.{m.name}")
    from app.db import Base

    return dict(Base.metadata.tables)


def _migration_module():
    """Load the revision by PATH — `alembic/versions` is not an importable
    package, so a normal import would fail regardless of the file being there."""
    import importlib.util
    import pathlib

    path = (pathlib.Path(__file__).resolve().parents[1]
            / "alembic" / "versions" / "r94childrls_installment_child_rls.py")
    spec = importlib.util.spec_from_file_location("r94childrls", path)
    assert spec and spec.loader, f"migration not found at {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _child_tables_from_migration() -> set[str]:
    return {t for t, _fk in _migration_module().CHILD_TABLES}


#: Tables that reference `installments` but are DELIBERATELY not tenant-scoped.
#: `customer_risk_signals` is the cross-tenant risk network: a consuming shop is
#: meant to read signals raised by OTHER shops, which is the entire feature, and
#: its `source_tenant_id` is nullable and explicitly "never surfaced to consuming
#: tenants". Giving it the parent-scoped policy below would restrict every shop
#: to its own signals and quietly turn the network off. Its RLS posture is a
#: separate decision — see the note in the pass report — and forcing it into the
#: child pattern would be the wrong answer confidently applied.
DELIBERATELY_SHARED = {"customer_risk_signals"}


def test_every_installments_child_without_tenant_id_is_covered():
    tables = _all_mapped_tables()
    covered = _child_tables_from_migration() | DELIBERATELY_SHARED

    uncovered: list[str] = []
    for name, table in tables.items():
        if "tenant_id" in table.columns:
            continue                       # r73 covers these by column
        # Does anything here point at `installments`?
        points_at_installments = any(
            fk.column.table.name == "installments"
            for col in table.columns for fk in col.foreign_keys
        )
        if points_at_installments and name not in covered:
            uncovered.append(name)

    assert not uncovered, (
        "these are children of `installments` with no `tenant_id` of their own, "
        f"and no RLS policy: {sorted(uncovered)}\n"
        "r73 scopes by tenant_id column and cannot see them. Add them to "
        "CHILD_TABLES in r94childrls_installment_child_rls.py, or the tenant "
        "path will be denied every write to them the moment RLS is on."
    )


def test_the_migration_still_names_the_two_that_broke():
    covered = _child_tables_from_migration()
    assert "installment_items" in covered
    assert "installment_guarantors" in covered


def test_the_policy_reaches_tenancy_through_the_parent():
    """The clause must join to `installments`, not invent a tenant_id."""
    sql = _migration_module()._clause("installment_id")
    assert "FROM installments" in sql
    assert "i.tenant_id" in sql
    # And it must keep r73's fail-open contract for the platform path, which
    # runs with no GUC set and must not be locked out of its own tables.
    assert "current_setting('app.tenant_id', true), '') IS NULL" in sql
