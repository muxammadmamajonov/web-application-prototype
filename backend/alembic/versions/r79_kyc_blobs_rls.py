"""RLS tenant-isolation policy on kyc_blobs (#124 / #95 follow-up)

kyc_blobs (#113, added in r78) carries a tenant_id but was created after r73's RLS
rollout, so it shipped WITHOUT the tenant_isolation policy — the one tenant-scoped
table missing the DB-level isolation backstop. This extends the same ENABLE RLS +
tenant_isolation policy to it. (muddato_app already has table privileges on it via
r73's ALTER DEFAULT PRIVILEGES, which covers tables created by later migrations.)

The USING/WITH CHECK clauses are copied verbatim from r73 so kyc_blobs behaves
identically: no GUC → all rows (platform path); else tenant_id must match the
request's app.tenant_id. kyc_blobs.tenant_id is NOT NULL, so the IS NULL global
branch never applies here.

Revision ID: r79kycblobsrls
Revises: r78kycblobs
"""
from __future__ import annotations

from alembic import op

revision = "r79kycblobsrls"
down_revision = "r78kycblobs"
branch_labels = None
depends_on = None

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
    op.execute("SET lock_timeout = '5s'")
    op.execute("ALTER TABLE kyc_blobs ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON kyc_blobs")
    op.execute(
        f"CREATE POLICY tenant_isolation ON kyc_blobs "
        f"USING ({_USING}) WITH CHECK ({_CHECK})"
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '5s'")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON kyc_blobs")
    op.execute("ALTER TABLE kyc_blobs DISABLE ROW LEVEL SECURITY")
