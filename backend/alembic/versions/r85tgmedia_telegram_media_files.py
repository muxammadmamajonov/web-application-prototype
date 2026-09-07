"""media_files — Telegram-group media references + tenants.telegram_topic_id (#814)

Revision ID: r85tgmedia
Revises: r84webhookdlq
Create Date: 2026-06-13

New, fully additive. ``media_files`` records WHERE a tenant's file lives in
Telegram (chat + topic + message + file_id) plus an optional durable fallback
copy (object_key / data). ``tenants.telegram_topic_id`` caches each tenant's
lazily-created forum topic. Tenant-isolation RLS uses the same policy as
r73/r79/r84 (nullable tenant_id → the platform / General-topic branch).
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "r85tgmedia"
down_revision = "r84webhookdlq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("telegram_topic_id", sa.Integer(), nullable=True))

    op.create_table(
        "media_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("message_thread_id", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("file_id", sa.String(256), nullable=True),
        sa.Column("file_unique_id", sa.String(64), nullable=True),
        sa.Column("bot_owner", sa.String(16), nullable=False, server_default="platform"),
        sa.Column("original_filename", sa.String(256), nullable=True),
        sa.Column("mime_type", sa.String(128), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("entity_type", sa.String(32), nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="stored"),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("object_key", sa.String(256), nullable=True),
        sa.Column("data", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_media_files_tenant_id", "media_files", ["tenant_id"])
    op.create_index("ix_media_files_file_unique_id", "media_files", ["file_unique_id"])
    op.create_index("ix_media_files_entity", "media_files", ["entity_type", "entity_id"])

    # Tenant-isolation RLS (same policy as r73/r79/r84; nullable tenant_id → the
    # platform / General-topic branch). muddato_app already holds table privileges
    # on tables created by later migrations via r73's ALTER DEFAULT PRIVILEGES.
    op.execute("SET lock_timeout = '5s'")
    op.execute("ALTER TABLE media_files ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON media_files")
    op.execute(
        "CREATE POLICY tenant_isolation ON media_files "
        "USING (NULLIF(current_setting('app.tenant_id', true), '') IS NULL "
        "OR tenant_id IS NULL "
        "OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (NULLIF(current_setting('app.tenant_id', true), '') IS NULL "
        "OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON media_files")
    op.drop_index("ix_media_files_entity", table_name="media_files")
    op.drop_index("ix_media_files_file_unique_id", table_name="media_files")
    op.drop_index("ix_media_files_tenant_id", table_name="media_files")
    op.drop_table("media_files")
    op.drop_column("tenants", "telegram_topic_id")
