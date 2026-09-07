"""customer_status += 'pending' + tenant_users.invitation_status

Revision ID: d4e5pending
Revises: c3d4plan
Create Date: 2026-05-28 22:30:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "d4e5pending"
down_revision = "c3d4plan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── customer_status: add 'pending' (must come before 'active' for
    #    moderation flows so the default insert is the more cautious one).
    #    Postgres requires ALTER TYPE … ADD VALUE outside a tx block.
    op.execute("ALTER TYPE customer_status ADD VALUE IF NOT EXISTS 'pending' BEFORE 'active'")

    # ── tenant_users.invitation_status — tracks whether a staff member
    #    has actually accepted (logged in via the invitation link) vs.
    #    just being seeded by the owner. Mirrors the StaffRow API.
    op.add_column("tenant_users", sa.Column(
        "invitation_status", sa.String(16), nullable=False, server_default="active",
    ))


def downgrade() -> None:
    # Postgres enums can't drop values cleanly; we recreate the type
    # without 'pending'. Any customers in that state get upgraded to
    # 'active' (the optimistic default) so we don't lose rows.
    op.execute("UPDATE customers SET status = 'active' WHERE status = 'pending'")
    op.execute("ALTER TYPE customer_status RENAME TO customer_status_old")
    op.execute("CREATE TYPE customer_status AS ENUM ('active', 'blocked')")
    op.execute(
        "ALTER TABLE customers ALTER COLUMN status TYPE customer_status "
        "USING status::text::customer_status"
    )
    op.execute("DROP TYPE customer_status_old")

    op.drop_column("tenant_users", "invitation_status")
