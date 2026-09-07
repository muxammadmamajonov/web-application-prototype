"""RLS policies for the installment CHILD tables (production outage fix).

THE OUTAGE. Creating a contract failed in production with:

    asyncpg.exceptions.InsufficientPrivilegeError:
    new row violates row-level security policy for table "installment_items"

`r73_rls_tenant_isolation` scopes every table that CARRIES a `tenant_id`
column. `installment_items` and `installment_guarantors` do not carry one —
they are children of `installments` and inherit their tenancy through it — so
that migration correctly skipped them and, on our own schema, left RLS off.

Row-level security was nevertheless ENABLED on them in the live database
(Supabase turns it on for tables by default). RLS enabled with **no policy**
denies everything to a non-superuser, so the restricted `muddato_app` role
could read the parent contract and then not write a single line item. The
tenant path is the only path that writes these tables, which is why the core
workflow broke while everything platform-side (running as `postgres`, which
bypasses RLS) kept working.

THE POLICY. Tenancy is reached through the parent, which is where it actually
lives — a line item belongs to whichever tenant owns its installment:

    EXISTS (SELECT 1 FROM installments i
             WHERE i.id = <fk> AND i.tenant_id = <the request's tenant>)

The subquery is itself subject to `installments`' own policy, so a cross-tenant
write is refused twice: once because the parent is invisible, and again by this
WITH CHECK. That is stricter than a `tenant_id` column would have been, because
the item cannot disagree with its parent about who owns it.

The no-GUC branch matches `r73`'s deliberate fail-open contract: platform,
scheduler and webhook work never calls `resolve_tenant`, runs as `postgres`,
and must keep seeing everything (see app/db.py).

IDEMPOTENT on purpose. The live database already has RLS enabled here while a
fresh one does not, so `ENABLE` is unconditional (it is a no-op when already on)
and the policy is dropped-if-exists before creation. Running this against either
state converges to the same result.
"""
from alembic import op

revision = "r94childrls"
down_revision = "r93payidx"
branch_labels = None
depends_on = None

#: (table, fk column) — each a child of `installments`.
CHILD_TABLES: tuple[tuple[str, str], ...] = (
    ("installment_items", "installment_id"),
    ("installment_guarantors", "installment_id"),
)


def _clause(fk: str) -> str:
    """Visible/writable when the request has no tenant scope (platform path),
    or when the parent installment belongs to the scoped tenant."""
    return (
        "NULLIF(current_setting('app.tenant_id', true), '') IS NULL "
        "OR EXISTS (SELECT 1 FROM installments i "
        f"WHERE i.id = {fk} "
        "AND i.tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def upgrade() -> None:
    for table, fk in CHILD_TABLES:
        # The role must be able to reach the table at all before a policy can
        # let it through; r73 granted schema-wide, but re-granting is harmless
        # and makes this migration self-sufficient on a partially-migrated DB.
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO muddato_app")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({_clause(fk)}) WITH CHECK ({_clause(fk)})"
        )


def downgrade() -> None:
    for table, _fk in CHILD_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
