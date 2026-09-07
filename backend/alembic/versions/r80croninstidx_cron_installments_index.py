"""Composite index on installments(status, next_due_date) for the daily cron (#140)

The daily cron sweeps installments CROSS-TENANT by (status, next_due_date):
- mark_overdue   — status=active   AND next_due_date < today
- apply_late_fees — status=overdue AND next_due_date IS NOT NULL
- send_reminders  — status=overdue AND next_due_date IS NOT NULL (+ tenant filter)

Without a covering index these are sequential scans on a table that grows with
every installment. This adds ix_installments_status_next_due, built CONCURRENTLY
(via an autocommit block) so it doesn't lock the table during deploy.

Revision ID: r80croninstidx
Revises: r79kycblobsrls
"""
from __future__ import annotations

from alembic import op

revision = "r80croninstidx"
down_revision = "r79kycblobsrls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CONCURRENTLY can't run inside a transaction; autocommit_block exits the
    # migration's transaction for the duration of the statement.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_installments_status_next_due "
            "ON installments (status, next_due_date)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_installments_status_next_due")
