"""Make tenant_subscriptions.starts_at / ends_at timezone-aware (Round 33)

The columns were TIMESTAMP WITHOUT TIME ZONE, but the app writes timezone-aware
UTC datetimes — asyncpg raised "can't subtract offset-naive and offset-aware
datetimes", crashing demo/trial provisioning. Convert to timestamptz, treating
the existing naive values as UTC.

Revision ID: r33subtzaware
Revises: r32catalogschema
Create Date: 2026-05-30 12:30:00+00:00
"""
from __future__ import annotations

from alembic import op

revision = "r33subtzaware"
down_revision = "r32catalogschema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tenant_subscriptions "
        "ALTER COLUMN starts_at TYPE timestamptz USING starts_at AT TIME ZONE 'UTC', "
        "ALTER COLUMN ends_at   TYPE timestamptz USING ends_at   AT TIME ZONE 'UTC'"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE tenant_subscriptions "
        "ALTER COLUMN starts_at TYPE timestamp USING starts_at AT TIME ZONE 'UTC', "
        "ALTER COLUMN ends_at   TYPE timestamp USING ends_at   AT TIME ZONE 'UTC'"
    )
