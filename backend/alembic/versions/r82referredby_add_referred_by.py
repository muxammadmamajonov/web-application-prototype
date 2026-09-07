"""add users.referred_by_user_id for referral capture (#196)

Revision ID: r82referredby
Revises: r81usrtokrev
Create Date: 2026-06-08

A nullable self-FK on users capturing who referred this user, stamped from the
``?startapp=ref_<code>`` deep link at /start. Stamp-then-materialize: the
``Referral`` row is created later, at the referee's first paid subscription, when
their tenant exists. ON DELETE SET NULL so removing the referrer never
cascade-deletes the referee. Fully backward-compatible (NULL = organic signup).
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "r82referredby"
down_revision = "r81usrtokrev"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("referred_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        op.f("ix_users_referred_by_user_id"), "users", ["referred_by_user_id"],
    )
    op.create_foreign_key(
        "fk_users_referred_by_user_id", "users", "users",
        ["referred_by_user_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_referred_by_user_id", "users", type_="foreignkey")
    op.drop_index(op.f("ix_users_referred_by_user_id"), table_name="users")
    op.drop_column("users", "referred_by_user_id")
