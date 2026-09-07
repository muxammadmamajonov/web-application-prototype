"""Track Telegram notification delivery attempts truthfully.

Revision ID: r98notifdelivery
Revises: r97rescheddue
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r98notifdelivery"
down_revision = "r97rescheddue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "notifications",
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("notifications", "delivered_at")
    op.drop_column("notifications", "last_attempt_at")
    op.drop_column("notifications", "attempt_count")
