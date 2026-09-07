"""Gapless sequential invoice numbers + a transaction-safe counter (#99)

UZ legal invoices need gapless sequential numbering. A plain DB SEQUENCE leaves
gaps (a rolled-back transaction consumes a value), so numbering goes through a
``sequence_counters`` row bumped by an atomic upsert-increment inside the invoice
transaction. Adds ``invoices.invoice_number`` (unique) + the counter table.

Existing invoices keep a NULL number (they predate the scheme); new invoices are
numbered from issue. The exact human format + VAT/QQS wording remain a
[LEGAL_REVIEW] item for a UZ accountant — out of scope for this migration.

Revision ID: r71invoiceno
Revises: r70paymebidx
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r71invoiceno"
down_revision = "r70paymebidx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sequence_counters",
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    op.add_column("invoices", sa.Column("invoice_number", sa.String(32), nullable=True))
    op.create_index(
        op.f("ix_invoices_invoice_number"), "invoices", ["invoice_number"], unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_invoices_invoice_number"), table_name="invoices")
    op.drop_column("invoices", "invoice_number")
    op.drop_table("sequence_counters")
