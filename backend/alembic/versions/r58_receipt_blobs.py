"""Receipt blob store for cash-payment receipt images (R58)

The cash-receipt upload previously tried to stuff a base64 ``data:`` URL into
``payments.receipt_image_url VARCHAR(500)`` — which overflows on any real
photo, so the feature was 100% broken. Receipt bytes now live in their own
``receipt_blobs`` table; ``payments.receipt_image_url`` holds a short,
HMAC-signed capability URL that an unauthenticated-but-signed endpoint serves.

Revision ID: r58receiptblobs
Revises: r57paymentextrefuniq
Create Date: 2026-05-31 10:30:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "r58receiptblobs"
down_revision = "r57paymentextrefuniq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "receipt_blobs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="fk_receipt_blobs_tenant_id_tenants", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_receipt_blobs_tenant_id", "receipt_blobs", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_receipt_blobs_tenant_id", table_name="receipt_blobs")
    op.drop_table("receipt_blobs")
