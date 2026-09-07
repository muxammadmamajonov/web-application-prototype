"""Blind index for the Payme secret → O(1) webhook tenant lookup (#84)

The Payme webhook authenticated by scanning every tenant's stored secret and
Fernet-decrypting each one per call — O(tenants) crypto on an unauthenticated
endpoint (latency + an amplification-DoS vector). This adds an indexed keyed-HMAC
blind index of the secret so the webhook finds the tenant in one indexed lookup,
then does exactly one decrypt + constant-time compare.

Existing rows are backfilled here (decrypt + HMAC) so the webhook is O(1) the
instant the new code deploys — no Payme-auth downtime window. The backfill is
additive + idempotent (only fills NULL); on a fresh DB it's a no-op.

Revision ID: r70paymebidx
Revises: r69piienc
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r70paymebidx"
down_revision = "r69piienc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant_settings", sa.Column("payme_secret_bidx", sa.String(64), nullable=True))
    op.create_index(
        op.f("ix_tenant_settings_payme_secret_bidx"),
        "tenant_settings", ["payme_secret_bidx"], unique=False,
    )

    # Backfill the blind index for existing tenants (decrypt + keyed HMAC) so the
    # webhook is O(1) immediately. Bounded (hundreds of rows) + idempotent.
    from app.core.crypto import blind_index, decrypt_secret

    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, payme_secret_key FROM tenant_settings "
        "WHERE payme_secret_key IS NOT NULL AND payme_secret_bidx IS NULL"
    )).mappings().all()
    for row in rows:
        secret = decrypt_secret(row["payme_secret_key"])
        if not secret:
            continue
        conn.execute(
            sa.text("UPDATE tenant_settings SET payme_secret_bidx = :b WHERE id = :id"),
            {"b": blind_index(secret, context="payme_secret"), "id": row["id"]},
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_tenant_settings_payme_secret_bidx"), table_name="tenant_settings")
    op.drop_column("tenant_settings", "payme_secret_bidx")
