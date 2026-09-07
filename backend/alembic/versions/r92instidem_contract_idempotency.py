"""Contract-creation idempotency key + partial unique index (F-12)

A double-tap or network retry of POST /installments could create two contracts.
The client now sends a stable ``Idempotency-Key`` header; this adds the column it
lands in plus a partial unique index so a replayed create collides at the DB and
the endpoint returns the existing contract instead of a duplicate. Mirrors the
``media_files`` idempotency backstop (r87).

Metadata-only (nullable column + partial unique index over NOT-NULL keys, which is
empty on existing rows) — no table rewrite, instant on a populated table.

Revision ID: r92instidem
Revises: r91smsdedup
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r92instidem"
down_revision = "r91smsdedup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("installments", sa.Column("idempotency_key", sa.String(length=128), nullable=True))
    op.create_index(
        "uq_installments_idem",
        "installments",
        ["tenant_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_installments_idem", table_name="installments")
    op.drop_column("installments", "idempotency_key")
