"""Partial index on payments.approved_at for approved rows (audit/perf)

Platform revenue aggregates (platform.overview, platform.platform_payments) run
cross-tenant SUM / GROUP BY date_trunc(approved_at) filtered to status='approved'.
Without an approved_at index those queries seq-scan the whole payments table. This
adds a partial index ``ix_payments_approved_at (approved_at) WHERE status='approved'``
matching the declaration on the Payment model.

Built CONCURRENTLY (outside a transaction) so it never blocks live payment writes
during the deploy release_command; IF NOT EXISTS keeps it idempotent.

Revision ID: r93payidx
Revises: r92instidem
"""
from __future__ import annotations

from alembic import op

revision = "r93payidx"
down_revision = "r92instidem"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CONCURRENTLY cannot run inside a transaction — alembic wraps each migration
    # in one, so open an autocommit block for this statement only.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_payments_approved_at "
            "ON payments (approved_at) WHERE status = 'approved'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_payments_approved_at")
