"""Encrypt payment provider secrets at rest (R61)

click_secret_key / payme_secret_key were stored in plaintext. Widen the columns
to hold a Fernet token and encrypt any existing plaintext values in place.
Reads use decrypt-or-passthrough, so this is backwards-compatible.

Revision ID: r61encsecrets
Revises: r60cronlocks
Create Date: 2026-05-31 12:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r61encsecrets"
down_revision = "r60cronlocks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "tenant_settings", "click_secret_key",
        type_=sa.String(512), existing_type=sa.String(255), existing_nullable=True,
    )
    op.alter_column(
        "tenant_settings", "payme_secret_key",
        type_=sa.String(512), existing_type=sa.String(255), existing_nullable=True,
    )

    # Encrypt existing plaintext values in place (online mode only — offline
    # --sql can't run a data migration).
    if op.get_context().as_sql:
        return
    from app.core.crypto import encrypt_secret, is_encrypted

    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, click_secret_key, payme_secret_key FROM tenant_settings "
        "WHERE click_secret_key IS NOT NULL OR payme_secret_key IS NOT NULL"
    )).mappings().all()
    for r in rows:
        updates: dict[str, str] = {}
        if r["click_secret_key"] and not is_encrypted(r["click_secret_key"]):
            updates["click_secret_key"] = encrypt_secret(r["click_secret_key"])
        if r["payme_secret_key"] and not is_encrypted(r["payme_secret_key"]):
            updates["payme_secret_key"] = encrypt_secret(r["payme_secret_key"])
        if updates:
            set_clause = ", ".join(f"{col} = :{col}" for col in updates)
            conn.execute(
                sa.text(f"UPDATE tenant_settings SET {set_clause} WHERE id = :id"),
                {**updates, "id": r["id"]},
            )


def downgrade() -> None:
    # Note: ciphertext stays in the (narrowed) column — lossy, but reads
    # passthrough so it remains functional only while the key is unchanged.
    op.alter_column(
        "tenant_settings", "click_secret_key",
        type_=sa.String(255), existing_type=sa.String(512), existing_nullable=True,
    )
    op.alter_column(
        "tenant_settings", "payme_secret_key",
        type_=sa.String(255), existing_type=sa.String(512), existing_nullable=True,
    )
