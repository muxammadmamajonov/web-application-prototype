"""Partial-unique index on payments.external_ref (R57 — webhook idempotency)

Provider transaction ids (Click trans id / Payme transaction) are stored in
``payments.external_ref``. Two concurrent provider webhooks for the same
transaction could previously double-insert/double-credit. This adds a DB-level
guard: ``external_ref`` must be unique among non-NULL values (cash payments
leave it NULL, and multiple NULLs are allowed).

The pre-create dedupe NULLs out the older row of any accidental duplicate so the
index can be built on a populated table without aborting the deploy. Duplicates
are not expected in practice; the dedupe is purely defensive.

Revision ID: r57paymentextrefuniq
Revises: r56userrefcode
Create Date: 2026-05-31 10:05:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r57paymentextrefuniq"
down_revision = "r56userrefcode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # Defensive: NULL out external_ref on all-but-the-newest of any duplicate
    # group so the unique index can be created. (Keeps the most recent row's
    # external_ref intact; older dupes lose their provider ref but keep the row.)
    conn.execute(sa.text(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY external_ref ORDER BY created_at DESC, id DESC
                   ) AS rn
            FROM payments
            WHERE external_ref IS NOT NULL
        )
        UPDATE payments p
           SET external_ref = NULL
          FROM ranked r
         WHERE p.id = r.id AND r.rn > 1
        """
    ))
    # Build the unique index CONCURRENTLY so the deploy doesn't take an ACCESS
    # EXCLUSIVE lock on the payments table. CONCURRENTLY cannot run inside a
    # transaction, so drop into an autocommit block (requires
    # transaction_per_migration in env.py). IF NOT EXISTS keeps a retried deploy
    # idempotent. The dedupe above is already committed by the time the block
    # opens, so CONCURRENTLY sees the de-duplicated data. (#76)
    with op.get_context().autocommit_block():
        op.execute(sa.text(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_payments_external_ref "
            "ON payments (external_ref) WHERE external_ref IS NOT NULL"
        ))


def downgrade() -> None:
    # DROP INDEX CONCURRENTLY likewise can't run in a transaction.
    with op.get_context().autocommit_block():
        op.execute(sa.text("DROP INDEX CONCURRENTLY IF EXISTS uq_payments_external_ref"))
