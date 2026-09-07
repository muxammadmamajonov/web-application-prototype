"""Record the early-payoff discount on the installment (#108)

Adds ``installments.payoff_discount_amount`` (nullable — safe, no backfill).
When set, ``recompute_state`` settles the loan at
``paid_amount >= principal - payoff_discount_amount`` so paying the discounted
early-payoff quote actually closes the contract instead of leaving a residual
balance equal to the discount (which kept dunning the customer).

Revision ID: r66payoff
Revises: r65wave4
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r66payoff"
down_revision = "r65wave4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "installments",
        sa.Column("payoff_discount_amount", sa.Numeric(14, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("installments", "payoff_discount_amount")
