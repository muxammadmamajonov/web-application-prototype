"""media_files.idempotency_key + partial-unique index (#817)

Revision ID: r87mediaidem
Revises: r86mfblob
Create Date: 2026-06-14

Adds a server-derived idempotency key so a retried upload reuses its existing
``media_files`` row instead of sending a duplicate document to Telegram. The
partial-unique index ``(tenant_id, idempotency_key) WHERE idempotency_key IS NOT
NULL`` is the concurrency backstop (a double-submit can persist at most one row);
sequential retries are short-circuited in ``store_tenant_media`` before any send.
Fully additive — existing rows have a NULL key.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r87mediaidem"
down_revision = "r86mfblob"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("media_files", sa.Column("idempotency_key", sa.String(128), nullable=True))
    op.create_index(
        "uq_media_files_idem",
        "media_files",
        ["tenant_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_media_files_idem", table_name="media_files")
    op.drop_column("media_files", "idempotency_key")
