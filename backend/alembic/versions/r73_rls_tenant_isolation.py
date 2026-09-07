"""Row-level security for tenant isolation (#95, defense-in-depth)

The app already scopes every tenant query with ``WHERE tenant_id = …`` (primary
defense, covered by the #79 isolation suite). This migration adds a *database*
layer so a handler that forgets the WHERE still can't leak another tenant's rows.

How it works
------------
* A NOLOGIN role ``muddato_app`` with no ``bypassrls``. ``postgres`` is granted
  membership so the app can ``SET LOCAL ROLE muddato_app`` for the duration of a
  tenant request (see app/db.py). Because it's NOLOGIN we never change
  ``DATABASE_URL`` or touch Supabase pooler auth — the connection stays
  ``postgres``; only the per-request transaction assumes the restricted role.
* RLS is ENABLEd (not FORCEd) on every tenant table, so the table owner
  (``postgres``) and any ``bypassrls`` role keep bypassing it — migrations, the
  scheduler worker, webhooks and platform-admin paths are unaffected. Only a
  transaction that has executed ``SET ROLE muddato_app`` is subject to the policy.
* Policies are FAIL-OPEN and global-aware:
    USING       no app.tenant_id GUC  → all rows (platform/scheduler path)
                tenant_id IS NULL      → shared/global row (e.g. global catalog,
                                         platform audit) visible to every tenant
                else                   → tenant_id must equal the request's tenant
    WITH CHECK  no GUC                 → platform path may write anything
                else                   → a written row must carry the request's
                                         tenant_id (so a tenant can't INSERT/UPDATE
                                         a row into another tenant or into the
                                         global/NULL space)
  ``NULLIF(…, '')`` treats an unset *or* empty GUC as "no tenant context", so a
  blank value can never reach ``''::uuid`` and error.

Applying this is safe before the app plumbing ships: the app runs as ``postgres``
until it opts in via SET ROLE, and ``postgres`` bypasses RLS — so an enabled but
un-exercised policy is inert. The plumbing is gated by ``settings.rls_enabled``.

Revision ID: r73rlsisolation
Revises: r72useruniq
"""
from __future__ import annotations

from alembic import op

revision = "r73rlsisolation"
down_revision = "r72useruniq"
branch_labels = None
depends_on = None

# Every public table carrying a tenant_id column (verified against the live
# schema). Six of these have a *nullable* tenant_id where NULL means a shared /
# global / platform row — the USING clause's ``tenant_id IS NULL`` keeps those
# readable to all tenants (don't let RLS hide the global catalog).
TENANT_TABLES: tuple[str, ...] = (
    "audit_log", "broadcasts", "catalog_models", "customers", "guarantors",
    "installments", "invitations", "invoices", "notification_channels",
    "notifications", "payments", "products", "receipt_blobs", "sms_messages",
    "stock_movements", "tenant_balance_txns", "tenant_payment_providers",
    "tenant_roles", "tenant_settings", "tenant_staff_invitations",
    "tenant_subscriptions", "tenant_topups", "tenant_users",
)

_USING = (
    "NULLIF(current_setting('app.tenant_id', true), '') IS NULL "
    "OR tenant_id IS NULL "
    "OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
)
_CHECK = (
    "NULLIF(current_setting('app.tenant_id', true), '') IS NULL "
    "OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
)


def upgrade() -> None:
    # ENABLE RLS takes a brief ACCESS EXCLUSIVE lock per table; bound it so the
    # release_command fails fast (no outage) rather than queueing behind a slow
    # query. The whole migration is one transaction (transaction_per_migration).
    op.execute("SET lock_timeout = '5s'")

    # 1) Restricted role + privileges (idempotent). Roles are cluster-scoped, so
    #    guard creation with a catalog check.
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'muddato_app') THEN "
        "CREATE ROLE muddato_app NOLOGIN; "
        "END IF; END $$;"
    )
    op.execute("GRANT muddato_app TO postgres")
    op.execute("GRANT USAGE ON SCHEMA public TO muddato_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO muddato_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO muddato_app")
    # Future tables/sequences (created as postgres by later migrations) inherit
    # the same grants so a new table isn't accidentally inaccessible under RLS.
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO muddato_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO muddato_app"
    )

    # 2) Enable RLS + (re)create the isolation policy on each tenant table.
    for t in TENANT_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} "
            f"USING ({_USING}) WITH CHECK ({_CHECK})"
        )


def downgrade() -> None:
    op.execute("SET lock_timeout = '5s'")
    for t in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
    # Best-effort teardown of the role: revoke privileges, then drop. Wrapped so a
    # leftover default-privilege ACL or dependent object doesn't wedge a rollback.
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM muddato_app")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM muddato_app")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM muddato_app")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM muddato_app")
    op.execute("REVOKE USAGE ON SCHEMA public FROM muddato_app")
    op.execute("REVOKE muddato_app FROM postgres")
    op.execute("DROP ROLE IF EXISTS muddato_app")
