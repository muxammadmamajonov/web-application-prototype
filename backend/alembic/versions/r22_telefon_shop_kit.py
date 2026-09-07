"""customer employment fields + guarantors + installment_guarantors (Round 22)

Revision ID: r22telefon
Revises: g7h8payoff
Create Date: 2026-05-29 02:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "r22telefon"
down_revision = "g7h8payoff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── customer employment / income ──────────────────────────────────────
    op.add_column("customers", sa.Column("employment_place", sa.String(200)))
    op.add_column("customers", sa.Column("monthly_income", sa.Numeric(14, 2)))
    op.add_column(
        "customers",
        sa.Column(
            "mehnat_daftarchasi_photo_urls",
            postgresql.JSONB,
            nullable=False,
            server_default="[]",
        ),
    )

    # ── guarantors ────────────────────────────────────────────────────────
    op.create_table(
        "guarantors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("phone", sa.String(32), nullable=False),
        sa.Column("passport_series", sa.String(8)),
        sa.Column("passport_number", sa.String(16)),
        sa.Column("jshshir", sa.String(14)),
        sa.Column("relationship", sa.String(64)),
        sa.Column("telegram_id", sa.BigInteger),
        sa.Column("address", sa.String(500)),
        sa.Column("photo_urls", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "phone", name="uq_guarantor_tenant_phone"),
    )
    op.create_index("ix_guarantors_jshshir", "guarantors", ["jshshir"])
    op.create_index("ix_guarantors_telegram_id", "guarantors", ["telegram_id"])

    # ── installment_guarantors join ──────────────────────────────────────
    op.create_table(
        "installment_guarantors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("installment_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("installments.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("guarantor_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("guarantors.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("sequence", sa.Integer, nullable=False, server_default="1"),
        sa.UniqueConstraint("installment_id", "guarantor_id", name="uq_installment_guarantor"),
    )


def downgrade() -> None:
    op.drop_table("installment_guarantors")
    op.drop_index("ix_guarantors_telegram_id", "guarantors")
    op.drop_index("ix_guarantors_jshshir", "guarantors")
    op.drop_table("guarantors")
    op.drop_column("customers", "mehnat_daftarchasi_photo_urls")
    op.drop_column("customers", "monthly_income")
    op.drop_column("customers", "employment_place")
