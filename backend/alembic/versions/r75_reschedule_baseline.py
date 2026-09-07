"""Reschedule re-anchor columns on installments (#119)

Adds ``installments.resched_base_paid`` and ``installments.resched_base_count``.
A reschedule changes ``monthly_amount`` mid-loan; ``recompute_state`` counts
future progress from the amount/count settled at the reschedule so the
already-paid installments aren't re-interpreted at the new (smaller) monthly
(which corrupted ``paid_count``). NULL = never rescheduled → legacy/unchanged
behaviour, so this is safe for every existing row.

Both changes are metadata-only (add nullable column) — no table rewrite, no lock
of consequence.

Revision ID: r75reschedbase
Revises: r74receiptobj
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r75reschedbase"
down_revision = "r74receiptobj"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "installments",
        sa.Column("resched_base_paid", sa.Numeric(14, 2), nullable=True),
    )
    op.add_column(
        "installments",
        sa.Column("resched_base_count", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("installments", "resched_base_count")
    op.drop_column("installments", "resched_base_paid")
