"""Durable retirement state for payment review notices.

Revision ID: re2noticeretry
Revises: re1prnotice
Create Date: 2026-08-10

Retirement was scheduled with FastAPI BackgroundTasks — process-local, so a
restart between the payment commit and the edit lost the work permanently. It
now lives in the row: an explicit state machine, an attempt budget, a backoff
schedule and a worker lease.

RLS stays fail-closed. Worker and platform access is by role, unchanged.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "re2noticeretry"
down_revision = "re1prnotice"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payment_review_notices", sa.Column("outcome", sa.String(16)))
    op.add_column("payment_review_notices", sa.Column("locale", sa.String(8)))
    op.add_column("payment_review_notices", sa.Column("body", sa.Text()))
    op.add_column("payment_review_notices",
                  sa.Column("attempt_count", sa.Integer(), nullable=False,
                            server_default="0"))
    op.add_column("payment_review_notices",
                  sa.Column("last_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("payment_review_notices",
                  sa.Column("next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("payment_review_notices",
                  sa.Column("lease_expires_at", sa.DateTime(timezone=True)))

    # `error` was Text; bound it, because only an exception class name or a
    # Telegram error code may ever be stored there.
    op.alter_column("payment_review_notices", "error",
                    existing_type=sa.Text(), type_=sa.String(64),
                    postgresql_using="left(error, 64)")

    # BACKFILL. `stale` was the old one-attempt dead end: those notices failed
    # once and were never retried, which is the defect. They are moved to
    # `retry` and made due immediately, so the new sweep picks them up — the
    # decision behind each is already final, so retiring the action is always
    # the right outcome. `pending` and `finalized` keep their meaning.
    #
    # `outcome` cannot be derived here without joining payments, and a wrong
    # label on a customer-visible message is worse than a delayed one; the
    # sweep skips a notice with no outcome and the backfill sets it from the
    # payment's own status.
    op.execute("""
        UPDATE payment_review_notices n
           SET state = 'retry',
               next_attempt_at = now(),
               outcome = CASE p.status
                            WHEN 'approved' THEN 'approved'
                            WHEN 'rejected' THEN 'rejected'
                         END,
               locale = COALESCE(n.locale, 'uz')
          FROM payments p
         WHERE p.id = n.payment_id
           AND n.state = 'stale'
           AND p.status IN ('approved', 'rejected')
    """)
    # A `stale` notice whose payment is somehow not decided has nothing to
    # retire; park it in the terminal bucket rather than inventing an outcome.
    op.execute("UPDATE payment_review_notices SET state = 'abandoned' "
               "WHERE state = 'stale'")

    op.drop_constraint("ck_prn_state", "payment_review_notices", type_="check")
    op.create_check_constraint(
        "ck_prn_state", "payment_review_notices",
        "state IN ('pending', 'retry', 'finalizing', 'finalized', 'abandoned')",
    )
    op.create_check_constraint(
        "ck_prn_outcome", "payment_review_notices",
        "outcome IS NULL OR outcome IN ('approved', 'rejected')",
    )
    op.create_index("ix_prn_due", "payment_review_notices",
                    ["state", "next_attempt_at"])
    print("[re2noticeretry] review-notice retirement is durable and retryable")


def downgrade() -> None:
    # RETRY METADATA IS DISCARDED, deliberately. It is derived delivery state:
    # attempt counts and backoff schedules describe how a message edit was
    # going, not what was decided. `Payment.status` remains authoritative, so
    # nothing about the money is lost.
    #
    # The cost is real and bounded: notices mid-retry revert to `stale` and the
    # old code will not retry them, leaving those actions live until a forward
    # re-upgrade. That is the behaviour this migration exists to fix, so a
    # downgrade restores the defect rather than causing a new one.
    op.drop_index("ix_prn_due", table_name="payment_review_notices")
    op.drop_constraint("ck_prn_outcome", "payment_review_notices", type_="check")
    op.execute("UPDATE payment_review_notices SET state = 'pending' "
               "WHERE state IN ('retry', 'finalizing')")
    op.execute("UPDATE payment_review_notices SET state = 'stale' "
               "WHERE state = 'abandoned'")
    op.drop_constraint("ck_prn_state", "payment_review_notices", type_="check")
    op.create_check_constraint(
        "ck_prn_state", "payment_review_notices",
        "state IN ('pending', 'finalized', 'stale')",
    )
    op.alter_column("payment_review_notices", "error",
                    existing_type=sa.String(64), type_=sa.Text())
    for column in ("lease_expires_at", "next_attempt_at", "last_attempt_at",
                   "attempt_count", "body", "locale", "outcome"):
        op.drop_column("payment_review_notices", column)
