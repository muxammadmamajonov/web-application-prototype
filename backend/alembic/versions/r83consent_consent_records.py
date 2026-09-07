"""consent_records — terms + national-ID processing consent capture (#168)

Revision ID: r83consent
Revises: r82referredby
Create Date: 2026-06-08

An append-only consent / lawful-basis log: one immutable row per acceptance. Backs the
terms-acceptance-at-signup and national-ID-processing consent (Customer KYC). New table,
fully additive — no impact on existing rows.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "r83consent"
down_revision = "r82referredby"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consent_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
        sa.Column("subject_kind", sa.String(16), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("terms_version", sa.String(32), nullable=False),
        sa.Column("lawful_basis", sa.String(32), nullable=False),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_consent_records_tenant_id", "consent_records", ["tenant_id"])
    op.create_index("ix_consent_records_subject_id", "consent_records", ["subject_id"])

    # (#168 / #73) Tenant-isolation RLS — same policy as every other tenant-scoped table
    # (verbatim from r73/r79). tenant_id is nullable (platform-level signup consent), so the
    # `tenant_id IS NULL` branch keeps those rows globally visible. muddato_app already has
    # privileges on later-created tables via r73's ALTER DEFAULT PRIVILEGES.
    op.execute("ALTER TABLE consent_records ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON consent_records")
    op.execute(
        "CREATE POLICY tenant_isolation ON consent_records "
        "USING (NULLIF(current_setting('app.tenant_id', true), '') IS NULL "
        "OR tenant_id IS NULL "
        "OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (NULLIF(current_setting('app.tenant_id', true), '') IS NULL "
        "OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.drop_index("ix_consent_records_subject_id", table_name="consent_records")
    op.drop_index("ix_consent_records_tenant_id", table_name="consent_records")
    op.drop_table("consent_records")
