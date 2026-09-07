"""media_files single-blob CHECK — never both object_key and data (#818)

Revision ID: r86mfblob
Revises: r85tgmedia
Create Date: 2026-06-14

Enforces the documented dual-write invariant at the DB level: a ``media_files`` row
holds at most ONE durable fallback copy — ``object_key`` (object store) XOR ``data``
(Postgres bytes), or neither for ``telegram_only``. Application code already upholds
this (``store_tenant_media`` writes one or the other), so this is fully additive — it
stops a future bug from persisting two divergent durable copies of the same financial
record.
"""
from __future__ import annotations

from alembic import op

revision = "r86mfblob"
down_revision = "r85tgmedia"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_media_files_single_blob",
        "media_files",
        "object_key IS NULL OR data IS NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_media_files_single_blob", "media_files", type_="check")
