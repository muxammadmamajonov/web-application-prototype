"""Indexes for the owner analytics endpoints (MUDD-134)

``GET /tenant/analytics/sales-by-category`` filters ``installments`` by
``(tenant_id, start_date)`` when a date range is given — the only existing
composite on ``installments`` leads with ``status`` (`ix_installments_tenant_status_due`)
or ends at ``created_at`` (`ix_installments_tenant_created`), neither of which
serves a `start_date` range without a full tenant-scoped scan.

``GET /tenant/analytics/summary``'s notification-health aggregate filters
``notifications`` and ``sms_messages`` by ``(tenant_id, created_at)`` over the
caller's window. `notifications` only had a lone `tenant_id` index plus the
`(tenant_id, kind, installment_id, created_at)` reminder-dedup composite, which
puts `kind`/`installment_id` between `tenant_id` and `created_at` and so cannot
serve an all-kinds date-range scan. `sms_messages` had no `(tenant_id,
created_at)` index at all — only the reminder-dedup partial index.

Built CONCURRENTLY (outside a transaction) so none of the three ever blocks a
live write during the deploy release_command; IF NOT EXISTS keeps the migration
idempotent — same shape as `rh1paytenantidx` (MUDD-88).

Revision ID: r99anlytidx
Revises: r98healtpl
"""
from __future__ import annotations

from alembic import op

revision = "r99anlytidx"
down_revision = "r98healtpl"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_installments_tenant_start_date "
            "ON installments (tenant_id, start_date)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_notifications_tenant_created "
            "ON notifications (tenant_id, created_at)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_sms_messages_tenant_created "
            "ON sms_messages (tenant_id, created_at)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_installments_tenant_start_date")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_notifications_tenant_created")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_sms_messages_tenant_created")
