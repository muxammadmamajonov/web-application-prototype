"""tenant_settings.early_payoff_discount_pct (Round 21.4)

Revision ID: g7h8payoff
Revises: f6g7contract
Create Date: 2026-05-29 01:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "g7h8payoff"
down_revision = "f6g7contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_settings",
        sa.Column(
            "early_payoff_discount_pct",
            sa.Numeric(5, 2),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_settings", "early_payoff_discount_pct")
