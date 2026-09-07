"""Receipt images → object storage reference (#96)

Adds ``receipt_blobs.object_key`` (the key of the image in a private object-storage
bucket) and makes ``receipt_blobs.data`` nullable, so a receipt stored in object
storage keeps only a reference in Postgres — not the bytes. Legacy rows keep their
``data`` blob until the backfill (scripts/backfill_receipts_to_object_store.py)
moves them out; the serve endpoint reads whichever is present.

Both changes are metadata-only (add nullable column + drop NOT NULL) — no table
rewrite, no lock of consequence.

Revision ID: r74receiptobj
Revises: r73rlsisolation
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r74receiptobj"
down_revision = "r73rlsisolation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "receipt_blobs",
        sa.Column("object_key", sa.String(length=256), nullable=True),
    )
    # Object-stored receipts carry no bytes in Postgres — `data` must be nullable.
    op.alter_column("receipt_blobs", "data", existing_type=sa.LargeBinary(), nullable=True)


def downgrade() -> None:
    # Re-tightening `data` to NOT NULL would fail if any object-stored rows exist;
    # leave it nullable on downgrade and just drop the reference column.
    op.drop_column("receipt_blobs", "object_key")
