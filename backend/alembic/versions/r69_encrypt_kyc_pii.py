"""Encrypt KYC government-ID PII at rest: widen columns + drop ciphertext indexes (#72)

The passport_* and jshshir columns on customers/guarantors now use the
EncryptedStr type (Fernet, transparent encrypt-on-write / decrypt-on-read). A
Fernet token for a short identifier is ~120 chars, so the historical
String(8..32) widths can't hold the ciphertext — widen them all to VARCHAR(255).
The jshshir indexes are dropped: an index over non-deterministic ciphertext is
useless, and nothing looks these up by equality (risk scoring keys its own
jshshir_hash column on customer_risk_signals).

This is a SCHEMA-only migration (safe to apply on any DB, runs in CI). Existing
plaintext rows keep reading via EncryptedStr's legacy passthrough; encrypting
those historical rows at rest is a separate one-time, idempotent backfill:
``python -m scripts.backfill_encrypt_pii`` (so the schema migration stays free of
data mutation — see #76).

Revision ID: r69piienc
Revises: r68ledgernum
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r69piienc"
down_revision = "r68ledgernum"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(op.f("ix_customers_jshshir"), table_name="customers")
    op.drop_index(op.f("ix_guarantors_jshshir"), table_name="guarantors")

    # Widen to hold Fernet ciphertext (VARCHAR(255)). Widening needs no USING.
    op.alter_column("customers", "passport_id",
                    existing_type=sa.String(32), type_=sa.String(255), existing_nullable=True)
    op.alter_column("customers", "passport_series",
                    existing_type=sa.String(8), type_=sa.String(255), existing_nullable=True)
    op.alter_column("customers", "passport_number",
                    existing_type=sa.String(16), type_=sa.String(255), existing_nullable=True)
    op.alter_column("customers", "jshshir",
                    existing_type=sa.String(14), type_=sa.String(255), existing_nullable=True)
    op.alter_column("guarantors", "passport_series",
                    existing_type=sa.String(8), type_=sa.String(255), existing_nullable=True)
    op.alter_column("guarantors", "passport_number",
                    existing_type=sa.String(16), type_=sa.String(255), existing_nullable=True)
    op.alter_column("guarantors", "jshshir",
                    existing_type=sa.String(14), type_=sa.String(255), existing_nullable=True)


def downgrade() -> None:
    # Narrowing back will (correctly) fail if encrypted ciphertext is present —
    # reverse the backfill (decrypt) before downgrading a live DB.
    op.alter_column("guarantors", "jshshir",
                    existing_type=sa.String(255), type_=sa.String(14), existing_nullable=True)
    op.alter_column("guarantors", "passport_number",
                    existing_type=sa.String(255), type_=sa.String(16), existing_nullable=True)
    op.alter_column("guarantors", "passport_series",
                    existing_type=sa.String(255), type_=sa.String(8), existing_nullable=True)
    op.alter_column("customers", "jshshir",
                    existing_type=sa.String(255), type_=sa.String(14), existing_nullable=True)
    op.alter_column("customers", "passport_number",
                    existing_type=sa.String(255), type_=sa.String(16), existing_nullable=True)
    op.alter_column("customers", "passport_series",
                    existing_type=sa.String(255), type_=sa.String(8), existing_nullable=True)
    op.alter_column("customers", "passport_id",
                    existing_type=sa.String(255), type_=sa.String(32), existing_nullable=True)

    op.create_index(op.f("ix_customers_jshshir"), "customers", ["jshshir"], unique=False)
    op.create_index(op.f("ix_guarantors_jshshir"), "guarantors", ["jshshir"], unique=False)
