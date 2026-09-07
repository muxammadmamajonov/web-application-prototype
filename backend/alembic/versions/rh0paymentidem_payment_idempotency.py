"""Payment idempotency key + partial unique index (MUDD-74)

The frontend has sent a stable ``Idempotency-Key`` header on
POST /tenant/payments since MUDD-56, but the server never read it — a
double-tap or network retry wrote a second Payment row for the same money,
double-counting the customer's balance. This adds the column the header lands
in plus a partial unique index so a replayed create collides at the DB and the
endpoint returns the existing payment instead of a duplicate. Mirrors the
contract-creation idempotency backstop (r92instidem / F-12).

Metadata-only (nullable column + partial unique index over NOT-NULL keys,
which is empty on existing rows) — no table rewrite, instant on a populated
table.

Revision ID: rh0paymentidem
Revises: rg0credsplit
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "rh0paymentidem"
down_revision = "rh0numsub"  # re-chained: rh0numsub landed on rg0credsplit first (PR #972)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("idempotency_key", sa.String(length=128), nullable=True))
    op.create_index(
        "uq_payments_idem",
        "payments",
        ["tenant_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_payments_idem", table_name="payments")
    op.drop_column("payments", "idempotency_key")
