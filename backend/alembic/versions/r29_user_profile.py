"""User profile fields — email, birthdate, gender (Round 29)

Revision ID: r29userprofile
Revises: r28inventory
Create Date: 2026-05-29 12:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "r29userprofile"
down_revision = "r28inventory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.String(255)))
    op.add_column("users", sa.Column("birthdate", sa.Date()))
    op.add_column("users", sa.Column("gender", sa.String(8)))


def downgrade() -> None:
    op.drop_column("users", "gender")
    op.drop_column("users", "birthdate")
    op.drop_column("users", "email")
