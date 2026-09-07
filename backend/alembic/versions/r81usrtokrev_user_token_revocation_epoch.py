"""add users.tokens_valid_from for session revocation (#123)

Revision ID: r81usrtokrev
Revises: r80croninstidx
Create Date: 2026-06-08

A nullable revocation epoch on users. A session JWT whose ``iat`` is older than
this column is rejected at auth, so a user (or an admin on their behalf) can
invalidate every outstanding token without waiting out the 14-day TTL. NULL keeps
existing tokens valid — fully backward-compatible.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r81usrtokrev"
down_revision = "r80croninstidx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("tokens_valid_from", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "tokens_valid_from")
