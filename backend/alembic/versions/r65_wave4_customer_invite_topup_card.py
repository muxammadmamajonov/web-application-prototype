"""Wave 4: customer-bound invitations + top-up destination card (#10, #15)

Adds (all nullable — safe, no backfill):
  • invitations.customer_id — binds a one-time invite to a pre-created customer
    so claiming attaches the joiner's Telegram id to THAT customer (which gates
    new contracts on the customer having joined).
  • tenant_topups.dest_card_id / dest_card_label — which platform receiving card
    a manual transfer was sent to, so the approver knows the destination.

Revision ID: r65wave4
Revises: r64hardening
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "r65wave4"
down_revision = "r64hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invitations",
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_invitations_customer_id", "invitations", "customers",
        ["customer_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_invitations_customer_id", "invitations", ["customer_id"])

    op.add_column("tenant_topups", sa.Column("dest_card_id", sa.String(length=64), nullable=True))
    op.add_column("tenant_topups", sa.Column("dest_card_label", sa.String(length=160), nullable=True))


def downgrade() -> None:
    op.drop_column("tenant_topups", "dest_card_label")
    op.drop_column("tenant_topups", "dest_card_id")
    op.drop_index("ix_invitations_customer_id", table_name="invitations")
    op.drop_constraint("fk_invitations_customer_id", "invitations", type_="foreignkey")
    op.drop_column("invitations", "customer_id")
