"""Billing idempotency + plan price precision (R59)

* UNIQUE(tenant_id, period_start) on invoices so a retried/double-fired billing
  cron can't double-bill a tenant. Dedupes any existing duplicates first
  (keeps the earliest-issued invoice per tenant+period).
* plans.price_monthly: unbounded NUMERIC -> NUMERIC(14, 2) to match every other
  money column and avoid format/rounding drift.

Revision ID: r59billingidem
Revises: r58receiptblobs
Create Date: 2026-05-31 11:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r59billingidem"
down_revision = "r58receiptblobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # Defensive dedupe: keep the earliest invoice per (tenant_id, period_start),
    # delete later duplicates so the unique constraint can be created.
    conn.execute(sa.text(
        """
        DELETE FROM invoices a
        USING invoices b
        WHERE a.tenant_id = b.tenant_id
          AND a.period_start = b.period_start
          AND (a.issued_at, a.id) > (b.issued_at, b.id)
        """
    ))
    op.create_unique_constraint(
        "uq_invoice_tenant_period", "invoices", ["tenant_id", "period_start"],
    )
    op.alter_column(
        "plans", "price_monthly",
        type_=sa.Numeric(14, 2), existing_nullable=False,
    )


def downgrade() -> None:
    op.drop_constraint("uq_invoice_tenant_period", "invoices", type_="unique")
    op.alter_column(
        "plans", "price_monthly",
        type_=sa.Numeric(), existing_nullable=False,
    )
