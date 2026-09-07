"""#83 — tenant bot tokens are Fernet-encrypted at rest via EncryptedStr.

DB-free guards: the column uses EncryptedStr(500); the type round-trips a real
bot-token-shaped value through ciphertext; and the backfill covers it.
"""
from __future__ import annotations

from pathlib import Path


def test_bot_token_column_is_encrypted_str_500() -> None:
    from app.core.crypto import EncryptedStr
    from app.models import TenantSettings

    col = TenantSettings.__table__.c.bot_token_ciphertext
    assert isinstance(col.type, EncryptedStr)
    assert col.type.impl.length == 500  # wide enough for the ciphertext


def test_encrypted_str_roundtrips_a_bot_token() -> None:
    from app.core.crypto import EncryptedStr, is_encrypted

    token = "test-bot-token-not-a-secret"
    t = EncryptedStr(500)
    stored = t.process_bind_param(token, None)
    assert stored is not None and is_encrypted(stored)    # enc:-prefixed at rest
    assert t.process_result_value(stored, None) == token  # plaintext on read


def test_backfill_covers_bot_tokens() -> None:
    src = (Path(__file__).resolve().parent.parent / "scripts" / "backfill_encrypt_pii.py").read_text(
        encoding="utf-8"
    )
    assert '"tenant_settings"' in src
    assert "bot_token_ciphertext" in src
