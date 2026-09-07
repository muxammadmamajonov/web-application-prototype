"""Distinct late-fee Payment method + per-day uniqueness (#121)

Late fees were stored as ``method=cash`` rows discriminated only by a ``notes``
prefix, so every collected-cash / GMV aggregation summed them as real income and
nothing at the DB level stopped a double cron-run from charging two fees per day.

This adds:
  * a dedicated ``payment_method`` enum value ``late_fee`` (added in an autocommit
    block — Postgres won't let a new enum value be *used* in the same transaction
    that adds it, and the partial index below references it),
  * ``payments.fee_date`` (the accrual day, set only on late-fee rows),
  * a partial unique index ``uq_payments_late_fee_day`` on
    ``(installment_id, fee_date) WHERE method = 'late_fee'`` so a concurrent second
    insert raises IntegrityError instead of double-charging.

All metadata-only (add enum value + nullable column + partial index) — no rewrite.

Revision ID: r76latefee
Revises: r75reschedbase
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r76latefee"
down_revision = "r75reschedbase"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD VALUE must be committed before it can be referenced by the index below.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE payment_method ADD VALUE IF NOT EXISTS 'late_fee'")
    op.add_column("payments", sa.Column("fee_date", sa.Date(), nullable=True))
    op.create_index(
        "uq_payments_late_fee_day",
        "payments",
        ["installment_id", "fee_date"],
        unique=True,
        postgresql_where=sa.text("method = 'late_fee'"),
    )


def downgrade() -> None:
    op.drop_index("uq_payments_late_fee_day", table_name="payments")
    op.drop_column("payments", "fee_date")
    # Postgres cannot DROP an enum value; 'late_fee' is left in payment_method
    # (harmless once the column + cron writes are reverted).
