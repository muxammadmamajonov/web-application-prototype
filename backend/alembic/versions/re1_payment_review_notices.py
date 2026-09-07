"""Payment review notices — the reference that lets a stale action be removed.

Revision ID: re1prnotice
Revises: re0docdeliv
Create Date: 2026-08-10

A receipt notification carries an inline action. Once the payment is approved
or rejected that action is stale, and Telegram will only edit a message if we
still know its chat and message id — hence this table.

RLS is fail-closed: an unset tenant context matches nothing. Platform and
background work is authorized by ROLE (it runs on the connection role and is
not subject to this policy), never by the absence of a session variable.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "re1prnotice"
down_revision = "re0docdeliv"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_review_notices",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("payment_id", sa.UUID(as_uuid=True),
                  sa.ForeignKey("payments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("state IN ('pending', 'finalized', 'stale')",
                           name="ck_prn_state"),
    )
    op.create_index("uq_prn_message", "payment_review_notices",
                    ["chat_id", "message_id"], unique=True)
    op.create_index("ix_prn_payment_state", "payment_review_notices",
                    ["payment_id", "state"])
    op.create_index("ix_payment_review_notices_payment_id", "payment_review_notices",
                    ["payment_id"])
    op.create_index("ix_payment_review_notices_tenant_id", "payment_review_notices",
                    ["tenant_id"])

    op.execute("SET lock_timeout = '5s'")
    op.execute("ALTER TABLE payment_review_notices ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON payment_review_notices")
    op.execute(
        "CREATE POLICY tenant_isolation ON payment_review_notices "
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )
    print("[re1prnotice] payment_review_notices created — fail-closed RLS")


def downgrade() -> None:
    # Safe to drop: losing these rows means a already-sent notification keeps
    # its stale button, which is a cosmetic regression, not data loss. No money
    # state lives here.
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON payment_review_notices")
    op.drop_index("ix_payment_review_notices_tenant_id", table_name="payment_review_notices")
    op.drop_index("ix_payment_review_notices_payment_id", table_name="payment_review_notices")
    op.drop_index("ix_prn_payment_state", table_name="payment_review_notices")
    op.drop_index("uq_prn_message", table_name="payment_review_notices")
    op.drop_table("payment_review_notices")
