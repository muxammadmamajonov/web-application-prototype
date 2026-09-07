"""webhook_dlq — dead-letter queue for failed webhooks (#754)

Revision ID: r84webhookdlq
Revises: r83consent
Create Date: 2026-06-12

Tracks webhook failures after MAX_WEBHOOK_RETRIES attempts. Moved to DLQ for
manual inspection and replay. New table, fully additive.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "r84webhookdlq"
down_revision = "r83consent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_dlq",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("provider", sa.String(32), nullable=False, index=True),
        sa.Column("method", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, default=0),
        sa.Column("last_error", sa.String(1024), nullable=True),
        sa.Column("dlq_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    # Tenant-isolation RLS (same policy as r83/r73)
    op.execute("ALTER TABLE webhook_dlq ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON webhook_dlq")
    op.execute(
        "CREATE POLICY tenant_isolation ON webhook_dlq "
        "USING (NULLIF(current_setting('app.tenant_id', true), '') IS NULL "
        "OR tenant_id IS NULL "
        "OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (NULLIF(current_setting('app.tenant_id', true), '') IS NULL "
        "OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_dlq_dlq_at", table_name="webhook_dlq")
    op.drop_index("ix_webhook_dlq_provider", table_name="webhook_dlq")
    op.drop_index("ix_webhook_dlq_tenant_id", table_name="webhook_dlq")
    op.drop_table("webhook_dlq")
