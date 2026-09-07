"""Inventory ledger — stock movements + low-stock alerting (Round 28)

Revision ID: r28inventory
Revises: r27billing
Create Date: 2026-05-29 08:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "r28inventory"
down_revision = "r27billing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    stock_kind = postgresql.ENUM(
        "in", "out", "sale", "return", "writeoff", "inventory",
        name="stock_movement_kind",
    )
    stock_kind.create(op.get_bind(), checkfirst=True)

    op.add_column("products", sa.Column("min_stock_level", sa.Integer()))

    op.create_table(
        "stock_movements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("kind", postgresql.ENUM(name="stock_movement_kind", create_type=False),
                  nullable=False),
        sa.Column("reason", sa.String(500)),
        sa.Column("by_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("related_installment_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("installments.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_stock_movements_tenant_id", "stock_movements", ["tenant_id"])
    op.create_index("ix_stock_movements_product_id", "stock_movements", ["product_id"])
    op.create_index("ix_stock_movements_kind", "stock_movements", ["kind"])
    op.create_index("ix_stock_movements_related_installment_id",
                    "stock_movements", ["related_installment_id"])


def downgrade() -> None:
    op.drop_index("ix_stock_movements_related_installment_id", table_name="stock_movements")
    op.drop_index("ix_stock_movements_kind", table_name="stock_movements")
    op.drop_index("ix_stock_movements_product_id", table_name="stock_movements")
    op.drop_index("ix_stock_movements_tenant_id", table_name="stock_movements")
    op.drop_table("stock_movements")
    op.drop_column("products", "min_stock_level")
    op.execute("DROP TYPE IF EXISTS stock_movement_kind")
