"""Cron lock table for single-instance job execution (R60)

APScheduler runs in-process on every app machine and does no cross-instance
coordination, so on >1 machine (deploy overlap / autoscale) each job fired
twice — duplicate reminders, double late fees. This table backs an atomic
"claim before run" lock with a TTL (so a crashed instance can't hold it
forever). Pooler-safe: a single upsert statement, no session state.

Revision ID: r60cronlocks
Revises: r59billingidem
Create Date: 2026-05-31 11:30:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r60cronlocks"
down_revision = "r59billingidem"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cron_locks",
        sa.Column("job_name", sa.String(length=64), primary_key=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("cron_locks")
