"""commission rate fields for staff-performance report (Round 24)

Revision ID: r24commission
Revises: r23risknet
Create Date: 2026-05-29 04:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "r24commission"
down_revision = "r23risknet"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_settings",
        sa.Column("commission_rate_pct", sa.Numeric(5, 2),
                  nullable=False, server_default="0"),
    )
    op.add_column(
        "tenant_users",
        sa.Column("commission_rate_pct", sa.Numeric(5, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_users", "commission_rate_pct")
    op.drop_column("tenant_settings", "commission_rate_pct")
