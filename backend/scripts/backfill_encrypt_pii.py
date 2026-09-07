"""One-time, idempotent backfill: encrypt secrets already at rest (#72 + #83).

Covers KYC government-ID PII (customers/guarantors) and tenant bot tokens.

Migration r69piienc widens the passport_*/jshshir columns, and the EncryptedStr
ORM type encrypts every NEW write. Run this once after deploying to encrypt the
rows written before the type existed (legacy plaintext):

    python -m scripts.backfill_encrypt_pii            # encrypt in place
    python -m scripts.backfill_encrypt_pii --dry-run  # report only, no writes

Idempotent: a value already carrying the ``enc:`` prefix is skipped, so re-runs
are safe. Reads/writes go through raw SQL on the SYNC engine so they bypass the
EncryptedStr type — we must see and write the stored value (plaintext or
ciphertext) verbatim, not the transparently-decrypted form.
"""
from __future__ import annotations

import sys

from sqlalchemy import create_engine, text

from app.config import settings
from app.core.crypto import encrypt_secret, is_encrypted

# Columns encrypted by EncryptedStr (must match the models).
_TARGETS = {
    "customers": ("passport_id", "passport_series", "passport_number", "jshshir"),
    "guarantors": ("passport_series", "passport_number", "jshshir"),
    "tenant_settings": ("bot_token_ciphertext",),  # (#83) tenant bot tokens
}


def main() -> int:
    dry = "--dry-run" in sys.argv
    engine = create_engine(settings.database_url_sync, future=True)
    changed_rows = 0
    changed_values = 0
    with engine.begin() as conn:
        for table, cols in _TARGETS.items():
            collist = ", ".join(cols)
            rows = conn.execute(text(f"SELECT id, {collist} FROM {table}")).mappings().all()
            for row in rows:
                updates = {
                    col: encrypt_secret(row[col])
                    for col in cols
                    if row[col] and not is_encrypted(row[col])
                }
                if not updates:
                    continue
                changed_rows += 1
                changed_values += len(updates)
                if dry:
                    continue
                set_clause = ", ".join(f"{c} = :{c}" for c in updates)
                conn.execute(
                    text(f"UPDATE {table} SET {set_clause} WHERE id = :id"),
                    {**updates, "id": row["id"]},
                )
    verb = "Would encrypt" if dry else "Encrypted"
    print(f"{verb} {changed_values} value(s) across {changed_rows} row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
