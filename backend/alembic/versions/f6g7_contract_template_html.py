"""tenant_settings.contract_template_html (Round 19)

Revision ID: f6g7contract
Revises: e5f6holidays
Create Date: 2026-05-29 00:30:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "f6g7contract"
down_revision = "e5f6holidays"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 200K char ceiling matches the validator in app/services/pdf.py.
    # Long enough for a fully styled contract; short enough that an
    # accidental megabyte paste fails at the DB layer too.
    op.add_column(
        "tenant_settings",
        sa.Column("contract_template_html", sa.String(length=200_000), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_settings", "contract_template_html")
