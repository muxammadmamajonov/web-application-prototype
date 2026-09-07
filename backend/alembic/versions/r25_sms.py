"""SMS fallback: notification channels + sms_messages + tenant SMS budget (Round 25)

Revision ID: r25sms
Revises: r24commission
Create Date: 2026-05-29 05:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "r25sms"
down_revision = "r24commission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── tenant SMS config ─────────────────────────────────────────────────
    op.add_column(
        "tenant_settings",
        sa.Column("sms_budget_uzs_per_month", sa.Integer(),
                  nullable=False, server_default="0"),
    )
    op.add_column(
        "tenant_settings",
        sa.Column("sms_sender_alpha", sa.String(16), nullable=True),
    )

    # ── notification_channels ─────────────────────────────────────────────
    # Enum columns auto-create their pg types as part of the table definition.
    op.create_table(
        "notification_channels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.Enum(
            "telegram", "sms", "email", name="notification_channel_type"),
            nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("opted_in_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("customer_id", "channel",
                            name="uq_notif_channel_customer_channel"),
    )
    op.create_index("ix_notification_channels_tenant_id",
                    "notification_channels", ["tenant_id"])
    op.create_index("ix_notification_channels_customer_id",
                    "notification_channels", ["customer_id"])

    # ── sms_messages ──────────────────────────────────────────────────────
    op.create_table(
        "sms_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("customers.id", ondelete="SET NULL")),
        sa.Column("installment_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("installments.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(40), nullable=False, server_default="manual"),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("text", sa.String(1000), nullable=False),
        sa.Column("parts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("cost_uzs", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("status", sa.Enum(
            "pending", "sent", "failed", name="sms_status"),
            nullable=False, server_default="pending"),
        sa.Column("provider_message_id", sa.String(64)),
        sa.Column("error", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sms_messages_tenant_id", "sms_messages", ["tenant_id"])
    op.create_index("ix_sms_messages_customer_id", "sms_messages", ["customer_id"])
    op.create_index("ix_sms_messages_kind", "sms_messages", ["kind"])
    op.create_index("ix_sms_messages_status", "sms_messages", ["status"])


def downgrade() -> None:
    op.drop_index("ix_sms_messages_status", table_name="sms_messages")
    op.drop_index("ix_sms_messages_kind", table_name="sms_messages")
    op.drop_index("ix_sms_messages_customer_id", table_name="sms_messages")
    op.drop_index("ix_sms_messages_tenant_id", table_name="sms_messages")
    op.drop_table("sms_messages")
    op.drop_index("ix_notification_channels_customer_id", table_name="notification_channels")
    op.drop_index("ix_notification_channels_tenant_id", table_name="notification_channels")
    op.drop_table("notification_channels")
    op.execute("DROP TYPE IF EXISTS sms_status")
    op.execute("DROP TYPE IF EXISTS notification_channel_type")
    op.drop_column("tenant_settings", "sms_sender_alpha")
    op.drop_column("tenant_settings", "sms_budget_uzs_per_month")
