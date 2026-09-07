"""Index payments (tenant_id, created_at) — the unfiltered tenant ledger read

`GET /tenant/payments` without a status filter runs
``WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?``. The existing
``ix_payments_tenant_status_created (tenant_id, status, created_at)`` cannot
serve it: with `status` in the middle, rows come back ordered by
`(status, created_at)`, so Postgres seq-scans the tenant's rows and sorts them.

Measured on a 60k-row shape-equivalent table, one tenant holding ~80% of rows
(the real "one busy shop" distribution):

    before  Seq Scan + top-N heapsort   8.766 ms   683 buffers
    after   Index Scan Backward         0.063 ms    53 buffers

That gap widens with the table, and `payments` is the fastest-growing table in
the schema — every contract leaves dozens of rows over its life, plus one
late-fee row per overdue day.

## Why a second index rather than reordering the existing one

Merging both into a single ``(tenant_id, created_at, status)`` was measured and
rejected. It serves the unfiltered read equally well (0.065 ms), but it degrades
the status-filtered read — which MUDD-84 just made a primary path by moving
status filtering and pagination server-side:

    status='rejected' OFFSET 200    two indexes 0.152 ms | merged 0.308 ms
    status='rejected' OFFSET 4000   two indexes 1.895 ms | merged 3.552 ms
                                    (Bitmap Index Scan)  | (Seq Scan + Sort)

At depth the merged index makes the planner abandon the index altogether. Saving
one index is not worth regressing the path the payments screen now depends on.

Built CONCURRENTLY (outside a transaction) so it never blocks live payment
writes during the deploy release_command; IF NOT EXISTS keeps it idempotent.

Revision ID: rh1paytenantidx
Revises: rh0paymentidem
"""
from __future__ import annotations

from alembic import op

revision = "rh1paytenantidx"
down_revision = "rh0paymentidem"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CONCURRENTLY cannot run inside a transaction — alembic wraps each migration
    # in one, so open an autocommit block for this statement only.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_payments_tenant_created "
            "ON payments (tenant_id, created_at)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_payments_tenant_created")
