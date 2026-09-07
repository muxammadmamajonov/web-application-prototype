"""Idempotent subscription top-up: unique provider_ref (#116)

A provider transaction must credit a tenant's prepaid balance at most once. The
crediting path is now row-locked (services/subscription_checkout.mark_topup_paid),
but this partial unique index is the DB-level backstop: a duplicate provider
delivery carrying the same ``provider_ref`` raises IntegrityError, which the
webhook treats as an idempotent no-op (mirrors ``payments.external_ref``).

Metadata-only (partial unique index, NULLs excluded) — no table rewrite.

Revision ID: r77topupref
Revises: r76latefee
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r77topupref"
down_revision = "r76latefee"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_tenant_topups_provider_ref",
        "tenant_topups",
        ["provider_ref"],
        unique=True,
        postgresql_where=sa.text("provider_ref IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_tenant_topups_provider_ref", table_name="tenant_topups")
