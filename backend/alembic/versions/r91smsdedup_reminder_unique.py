"""SMS reminder dedup backstop: dedup_day + partial unique index (F-09)

Overlapping cron runs (a job that outlives its 30-min lease, or a second worker
during a deploy) could both pass the read-then-write "already sent today?" check
and double-send a reminder SMS — real cost against tenant budgets. The scheduler
now *reserves* the dedup slot (a ``pending`` row) before calling the SMS provider,
inside a savepoint, so this partial unique index turns a duplicate send into an
IntegrityError the caller swallows — the INSERT collision is the guard.

``dedup_day`` is the calendar day the reminder belongs to. The index is unique over
(tenant_id, installment_id, kind, dedup_day) with NULLS NOT DISTINCT (PG15+), and
partial over only cron rows (``dedup_day IS NOT NULL``) that are live sends
(``status IN ('sent','pending')``) — a ``failed`` row leaves the index so a same-day
retry is still allowed.

Metadata-only (nullable column + partial unique index) — no table rewrite.

Revision ID: r91smsdedup
Revises: r90referraluq
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r91smsdedup"
down_revision = "r90referraluq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sms_messages", sa.Column("dedup_day", sa.Date(), nullable=True))
    op.create_index(
        "uq_sms_reminder_dedup",
        "sms_messages",
        ["tenant_id", "installment_id", "kind", "dedup_day"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("dedup_day IS NOT NULL AND status IN ('sent', 'pending')"),
    )


def downgrade() -> None:
    op.drop_index("uq_sms_reminder_dedup", table_name="sms_messages")
    op.drop_column("sms_messages", "dedup_day")
