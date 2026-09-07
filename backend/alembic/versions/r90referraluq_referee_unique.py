"""Referral double-pay backstop: partial UNIQUE(referee_tenant_id) (F-04)

"At most one Referral per referee tenant" was enforced only by an application
SELECT with no DB constraint, so two concurrent ``maybe_award_referral_conversion``
calls for the same referee could both pass the guard and both credit the referrer
(real money, twice). This replaces the plain lookup index with a *partial unique*
index (NULLs excluded — ``referee_tenant_id`` is nullable via FK ondelete=SET NULL,
and unattributed / no_payout rows legitimately carry NULL). The service now treats
the resulting IntegrityError as an idempotent no-op, so the INSERT collision — not
a racy SELECT — is the guard. The unique index also serves the lookup the old
non-unique index served, so the old one is dropped.

NOTE: the index build fails if duplicate non-NULL ``referee_tenant_id`` rows already
exist. The application guarded against this, so production should be clean; if the
build fails, dedupe ``referrals`` (keep the earliest per referee) before retrying.

Metadata-only (drop + partial unique index) — no table rewrite.

Revision ID: r90referraluq
Revises: r89phoneschema
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r90referraluq"
down_revision = "r89phoneschema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The old non-unique lookup index is superseded by the partial unique index
    # below (Postgres uses it for equality lookups too). IF EXISTS keeps this
    # safe across environments where autogenerate naming may have drifted.
    op.execute("DROP INDEX IF EXISTS ix_referrals_referee_tenant_id")
    op.create_index(
        "uq_referrals_referee_tenant",
        "referrals",
        ["referee_tenant_id"],
        unique=True,
        postgresql_where=sa.text("referee_tenant_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_referrals_referee_tenant", table_name="referrals")
    op.create_index(
        op.f("ix_referrals_referee_tenant_id"),
        "referrals",
        ["referee_tenant_id"],
        unique=False,
    )
