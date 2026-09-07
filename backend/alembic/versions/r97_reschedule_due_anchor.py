"""Keep a rescheduled contract's negotiated due-date anchor.

Revision ID: r97rescheddue
Revises: r96phoneuniq
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r97rescheddue"
down_revision = "r96phoneuniq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "installments",
        sa.Column("resched_base_due_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("installments", "resched_base_due_date")
