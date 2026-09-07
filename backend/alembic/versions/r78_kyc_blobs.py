"""Private store for KYC media — kyc_blobs (#113)

Customer/guarantor passport/ID/employment scans were uploaded to public
archive.org (the #107 proxy hid the URL but the bytes stayed world-readable).
They now live as ``kyc_blobs`` rows — bytes in a private object-storage bucket
(``object_key``) or, by default, in Postgres (``data``) — served only through the
tenant-authenticated /tenant/kyc-media proxy.

Revision ID: r78kycblobs
Revises: r77topupref
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "r78kycblobs"
down_revision = "r77topupref"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kyc_blobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=256), nullable=True),
        sa.Column("data", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kyc_blobs_tenant_id", "kyc_blobs", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_kyc_blobs_tenant_id", table_name="kyc_blobs")
    op.drop_table("kyc_blobs")
