"""Catalog product custom fields — tenant_settings.product_form_fields (Round 31)

Revision ID: r31productformfields
Revises: r30platformadmininvite
Create Date: 2026-05-29 14:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "r31productformfields"
down_revision = "r30platformadmininvite"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_settings",
        sa.Column(
            "product_form_fields",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )
    # Drop the server default once existing rows are backfilled — the ORM
    # supplies the default for new rows.
    op.alter_column("tenant_settings", "product_form_fields", server_default=None)


def downgrade() -> None:
    op.drop_column("tenant_settings", "product_form_fields")
