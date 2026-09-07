"""tenant_topups.provider_payload for the Payme subscription tx state machine (#858)

Revision ID: r88submtx
Revises: r87mediaidem
Create Date: 2026-06-15

Adds a nullable JSONB ``provider_payload`` to ``tenant_topups`` so the platform
Payme subscription webhook can persist the full JSON-RPC transaction state machine
(state, create/perform/cancel times, was_completed, cancel reason) on the top-up
row — mirroring ``payments.provider_payload`` for the per-tenant flow. The Payme
transaction id continues to live in the existing, uniquely-indexed
``provider_ref``. Fully additive — existing rows are NULL.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "r88submtx"
down_revision = "r87mediaidem"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_topups",
        sa.Column("provider_payload", postgresql.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_topups", "provider_payload")
