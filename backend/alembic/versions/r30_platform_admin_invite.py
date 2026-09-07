"""Platform-admin invitations — make invitations.tenant_id nullable (Round 30)

Revision ID: r30platformadmininvite
Revises: r29userprofile
Create Date: 2026-05-29 13:00:00+00:00
"""
from __future__ import annotations

from alembic import op
from sqlalchemy.dialects import postgresql

revision = "r30platformadmininvite"
down_revision = "r29userprofile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "invitations", "tenant_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    # Drop any platform-level invites first — they have no tenant_id and would
    # violate the restored NOT NULL constraint.
    op.execute("DELETE FROM invitations WHERE tenant_id IS NULL")
    op.alter_column(
        "invitations", "tenant_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
