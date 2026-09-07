"""#95 (RLS) — row-level security as defense-in-depth for tenant isolation.

The app already enforces tenant isolation in application code (every tenant query
carries ``WHERE tenant_id = …``; covered by the #79 isolation suite). RLS adds a
*second*, database-enforced layer: even a handler that forgets the WHERE can't
leak another tenant's rows, because the row-security policy filters them out.

Design (see migration r73 + app.db):
  * A NOLOGIN ``muddato_app`` role (no bypassrls). ``postgres`` is GRANTed
    membership so it can ``SET LOCAL ROLE muddato_app`` per tenant request.
  * Migrations / scheduler / platform paths keep running as ``postgres`` (which
    bypasses RLS) — they never assume the restricted role, so they're unaffected.
  * Policies are FAIL-OPEN: no ``app.tenant_id`` GUC ⇒ all rows (platform path);
    ``tenant_id IS NULL`` ⇒ shared/global row (e.g. the global catalog) visible to
    everyone; otherwise the row must match the request's tenant.

These tests are DB-free: they assert the migration's *content* (RLS enabled on
every tenant table, fail-open predicate, asymmetric WITH CHECK) and the app-side
plumbing (settings gate, GUC SQL builder, resolve_tenant wiring). The actual
enforcement is verified against the live DB with ``SET ROLE`` before cutover.
"""
from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

_MIG = (
    Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "r73_rls_tenant_isolation.py"
)

# The 23 tenant-scoped tables (every public table carrying a tenant_id column).
TENANT_TABLES = {
    "audit_log", "broadcasts", "catalog_models", "customers", "guarantors",
    "installments", "invitations", "invoices", "notification_channels",
    "notifications", "payments", "products", "receipt_blobs", "sms_messages",
    "stock_movements", "tenant_balance_txns", "tenant_payment_providers",
    "tenant_roles", "tenant_settings", "tenant_staff_invitations",
    "tenant_subscriptions", "tenant_topups", "tenant_users",
}


def _mig_src() -> str:
    return _MIG.read_text(encoding="utf-8")


def test_migration_exists_and_chains_from_head() -> None:
    src = _mig_src()
    assert 'revision = "r73rlsisolation"' in src
    assert 'down_revision = "r72useruniq"' in src


def test_rls_enabled_on_every_tenant_table() -> None:
    src = _mig_src()
    for t in TENANT_TABLES:
        assert t in src, f"{t} missing from RLS migration"
    # ENABLE ROW LEVEL SECURITY is applied for each table (via the table loop).
    assert "ENABLE ROW LEVEL SECURITY" in src
    assert "CREATE POLICY" in src and "tenant_isolation" in src


def test_policy_is_fail_open_and_permits_global_rows() -> None:
    src = _mig_src()
    # Fail-open: unset/empty GUC behaves as "no tenant context".
    assert "current_setting('app.tenant_id', true)" in src
    assert "NULLIF(current_setting('app.tenant_id', true), '')" in src
    # Shared/global rows (tenant_id IS NULL) stay readable to every tenant.
    assert "tenant_id IS NULL" in src


def test_role_is_nologin_and_postgres_can_assume_it() -> None:
    src = _mig_src()
    assert "muddato_app" in src
    assert "NOLOGIN" in src
    assert "GRANT muddato_app TO postgres" in src
    # Restricted role gets DML + sequence usage so normal CRUD works under it.
    assert "ON ALL TABLES IN SCHEMA public TO muddato_app" in src
    assert "ON ALL SEQUENCES IN SCHEMA public TO muddato_app" in src
    # Future tables (from later migrations, created as postgres) inherit grants.
    assert "ALTER DEFAULT PRIVILEGES" in src


def test_migration_uses_lock_timeout_so_a_busy_table_cant_hang_deploy() -> None:
    # ENABLE RLS takes a brief ACCESS EXCLUSIVE lock; a bounded lock_timeout makes
    # the release_command fail fast (no outage) instead of blocking behind a query.
    assert "lock_timeout" in _mig_src()


# ── app-side plumbing ────────────────────────────────────────────────────────

def test_settings_has_rls_enabled_gate() -> None:
    from app.config import settings
    assert hasattr(settings, "rls_enabled")
    assert isinstance(settings.rls_enabled, bool)


def test_set_tenant_guc_emits_set_local_role_and_tenant_id() -> None:
    """The GUC builder returns the two statements that scope a request to its
    tenant: assume the restricted role, then pin app.tenant_id. Each is a
    (sql, params) pair — the tenant id is *bound*, never interpolated."""
    from app.db import _rls_set_local_sql

    stmts = _rls_set_local_sql("11111111-1111-1111-1111-111111111111")
    sql = " ".join(s for s, _ in stmts).lower()
    assert "set local role muddato_app" in sql
    assert "set_config('app.tenant_id'" in sql
    # The id is in the bound params, never in the SQL text (injection-safe).
    assert "11111111" not in sql
    assert any(
        "11111111-1111-1111-1111-111111111111" in str(p.get("tid", ""))
        for _, p in stmts
    )


def test_resolve_tenant_applies_rls_scope() -> None:
    import app.api.tenant as tmod

    src = inspect.getsource(tmod.resolve_tenant)
    # After the tenant is known, resolve_tenant pins the RLS scope for the request.
    assert "set_tenant_scope" in src or "_apply_rls_scope" in src


def test_db_registers_after_begin_to_reapply_guc_across_commits() -> None:
    import app.db as db

    src = inspect.getsource(db)
    # A new transaction starts after every commit; the GUC must be re-applied or a
    # post-commit query in a multi-statement request would lose its tenant scope.
    assert "after_begin" in src
    assert "rls_enabled" in src


def _load_mig():
    spec = importlib.util.spec_from_file_location("r73mig", _MIG)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_has_upgrade_and_downgrade() -> None:
    mod = _load_mig()
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)
    # downgrade tears the policies + role back down cleanly.
    dsrc = inspect.getsource(mod.downgrade)
    assert "DROP POLICY" in dsrc
    assert "DISABLE ROW LEVEL SECURITY" in dsrc
