"""Migration reconciliation for r89phoneschema

Revision ID: r89phoneschema
Revises: r88submtx
Create Date: 2026-06-23

This is a reconciliation migration to align the migration history with
production database state. The actual schema changes for phone normalization
were previously applied and consolidated into earlier migrations.
"""
from __future__ import annotations

from alembic import op

revision = "r89phoneschema"
down_revision = "r88submtx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op: schema already matches target state from previous migrations."""
    pass


def downgrade() -> None:
    """No-op: downgrade not supported for reconciliation migration."""
    pass
