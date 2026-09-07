"""Document deliveries — idempotent sends and a Telegram file_id cache.

Revision ID: re0docdeliv
Revises: rd0invverifier
Create Date: 2026-08-10

INVARIANT: one live delivery row per (user, kind, object, document version);
one archived copy per (kind, object, version, bot).

INVARIANT: RLS fails closed — an unset tenant context matches nothing.
Platform work is authorized by role, not by an absent session variable.

Every row is a cache entry; the table is safe to drop and rebuild.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "re0docdeliv"
down_revision = "rd0invverifier"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_deliveries",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("object_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version", sa.String(64), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("tenant_id", sa.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE")),
        sa.Column("status", sa.String(16), nullable=False, server_default="sending"),
        sa.Column("chat_id", sa.BigInteger()),
        sa.Column("message_thread_id", sa.BigInteger()),
        sa.Column("message_id", sa.BigInteger()),
        sa.Column("file_id", sa.String(256)),
        sa.Column("file_unique_id", sa.String(64)),
        sa.Column("bot_owner", sa.String(16), nullable=False, server_default="platform"),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("purpose IN ('user_delivery', 'archive')",
                           name="ck_docdel_purpose"),
        sa.CheckConstraint("status IN ('sending', 'sent', 'failed')",
                           name="ck_docdel_status"),
        # The two purposes must not blur together, or the partial indexes below
        # stop meaning what they say.
        sa.CheckConstraint(
            "(purpose = 'archive' AND user_id IS NULL) OR "
            "(purpose = 'user_delivery' AND user_id IS NOT NULL)",
            name="ck_docdel_user_matches_purpose",
        ),
    )
    # THE IDEMPOTENCY. One live delivery of one document to one person; a failed
    # attempt is excluded so a genuine retry is still allowed.
    op.create_index(
        "uq_docdel_user_object", "document_deliveries",
        ["user_id", "kind", "object_id", "document_version"], unique=True,
        postgresql_where=sa.text("purpose = 'user_delivery' AND status <> 'failed'"),
    )
    # THE CACHE KEY. `bot_owner` is part of it because a `file_id` minted by one
    # bot is not resolvable by another — reusing it across bots returns "wrong
    # file identifier", which would look like data corruption rather than a
    # configuration mistake.
    op.create_index(
        "uq_docdel_archive", "document_deliveries",
        ["kind", "object_id", "document_version", "bot_owner"], unique=True,
        postgresql_where=sa.text("purpose = 'archive' AND status = 'sent'"),
    )
    op.create_index("ix_docdel_lookup", "document_deliveries", ["kind", "object_id"])
    op.create_index("ix_document_deliveries_tenant_id", "document_deliveries", ["tenant_id"])

    # TENANT ISOLATION — FAIL CLOSED.
    #
    # This table holds delivery metadata (user_id, Telegram chat/message/thread
    # ids and file_id), so an unscoped read is a cross-tenant disclosure.
    #
    # DELIBERATELY NARROWER than the policy on r73/r79/r84/r85. Those grant
    # access when `app.tenant_id` is unset, which means a `muddato_app`
    # transaction that never pinned a tenant reads EVERY tenant's rows. Here an
    # unset GUC matches nothing: `tenant_id = NULL` is NULL, never true.
    #
    # Legitimate platform/background work is authorized by ROLE, not by the
    # absence of a GUC — it runs on the ordinary connection role, which is not
    # subject to this policy (see app/db.py: those paths never call
    # resolve_tenant, so no SET LOCAL ROLE happens). A role is real
    # authorization; an unset session variable is the absence of information.
    #
    # A tenant session also cannot see platform-owned rows (`tenant_id IS NULL`)
    # — those belong to the General topic and to no shop.
    op.execute("SET lock_timeout = '5s'")
    op.execute("ALTER TABLE document_deliveries ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON document_deliveries")
    op.execute(
        "CREATE POLICY tenant_isolation ON document_deliveries "
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )
    print("[re0docdeliv] document_deliveries created — delivery idempotency + file_id cache")


def downgrade() -> None:
    # Safe to drop: every row is a cache entry, regenerable by sending again.
    # No money or legal state lives here.
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON document_deliveries")
    op.drop_index("ix_document_deliveries_tenant_id", table_name="document_deliveries")
    op.drop_index("ix_docdel_lookup", table_name="document_deliveries")
    op.drop_index("uq_docdel_archive", table_name="document_deliveries")
    op.drop_index("uq_docdel_user_object", table_name="document_deliveries")
    op.drop_table("document_deliveries")
