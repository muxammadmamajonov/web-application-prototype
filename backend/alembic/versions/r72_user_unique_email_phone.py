"""Normalized-unique email + phone on users (#95)

Tenant isolation + money CHECK constraints are handled elsewhere (app-level
isolation tested in #79; the ledger CHECKs shipped in #69). This migration covers
the third part of #95: ``User.email`` / ``User.phone`` were not unique, so the
same person could register twice (duplicate identity for billing/contact).

Two functional, partial, UNIQUE indexes — case-insensitive email and digits-only
phone — built CONCURRENTLY so a large users table isn't locked on deploy (#76).
Note: CONCURRENTLY fails (leaving an INVALID index) if duplicates already exist;
dedupe first if so. On a fresh DB this is a no-op-fast build.

RLS (the remaining part of #95) is intentionally deferred: the Supabase
transaction pooler forbids session GUCs (so a per-session ``app.tenant_id`` won't
work — it needs SET LOCAL per request), and a wrong policy would silently break
the cross-tenant paths (platform admin, scheduler, webhooks). It needs a careful
fail-open design + a live-DB test harness; app-level isolation already exists and
is covered by the #79 tenant-isolation suite.

Revision ID: r72useruniq
Revises: r71invoiceno
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r72useruniq"
down_revision = "r71invoiceno"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CONCURRENTLY can't run in a transaction — drop into an autocommit block
    # (enabled by transaction_per_migration in env.py, #76).
    with op.get_context().autocommit_block():
        op.execute(sa.text(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_users_email_lower "
            "ON users (lower(email)) WHERE email IS NOT NULL"
        ))
        op.execute(sa.text(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_users_phone_digits "
            "ON users (regexp_replace(phone, '[^0-9]', '', 'g')) WHERE phone IS NOT NULL"
        ))


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text("DROP INDEX CONCURRENTLY IF EXISTS uq_users_phone_digits"))
        op.execute(sa.text("DROP INDEX CONCURRENTLY IF EXISTS uq_users_email_lower"))
