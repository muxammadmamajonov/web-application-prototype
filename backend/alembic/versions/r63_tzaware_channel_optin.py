"""Make notification_channels.opted_in_at timezone-aware (R63)

Missed in R62. The channel opt-in save writes datetime.now(timezone.utc) into
this column, which the initial schema created as TIMESTAMP WITHOUT TIME ZONE —
so saving a customer's notification channels would hit the same asyncpg
"offset-naive vs offset-aware" 500. Convert it too (conditional, UTC-treated).

Revision ID: r63tzchannel
Revises: r62tzaware
Create Date: 2026-05-31 14:35:00+00:00
"""
from __future__ import annotations

from alembic import op

revision = "r63tzchannel"
down_revision = "r62tzaware"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF (SELECT data_type FROM information_schema.columns
              WHERE table_name = 'notification_channels' AND column_name = 'opted_in_at')
             = 'timestamp without time zone' THEN
            ALTER TABLE notification_channels
              ALTER COLUMN opted_in_at TYPE timestamptz USING opted_in_at AT TIME ZONE 'UTC';
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE notification_channels "
        "ALTER COLUMN opted_in_at TYPE timestamp USING opted_in_at AT TIME ZONE 'UTC'"
    )
