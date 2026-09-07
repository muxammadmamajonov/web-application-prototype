"""#72 — KYC government-ID PII is encrypted at rest and the risk hash is a keyed
HMAC that production refuses to run with the public dev salt.

DB-free guards (no live DB until #79): the EncryptedStr type round-trips through
Fernet with legacy-plaintext passthrough; the passport_*/jshshir columns use it;
the jshshir indexes are gone; the substring search no longer touches the
encrypted columns; hash_jshshir is HMAC-SHA256 keyed by RISK_HASH_SALT; and the
config validator hard-fails in production on the default salt.
"""
from __future__ import annotations

import hashlib
import hmac
import inspect

import pytest

# ── EncryptedStr type ────────────────────────────────────────────────────────

def test_encrypted_str_roundtrip() -> None:
    from app.core.crypto import EncryptedStr, is_encrypted

    t = EncryptedStr()
    stored = t.process_bind_param("AA1234567", None)
    assert stored is not None and is_encrypted(stored)        # ciphertext at rest
    assert t.process_result_value(stored, None) == "AA1234567"  # plaintext on read


def test_encrypted_str_legacy_plaintext_passthrough() -> None:
    from app.core.crypto import EncryptedStr

    # A row written before the type existed (no enc: prefix) still reads.
    assert EncryptedStr().process_result_value("12345678901234", None) == "12345678901234"


def test_encrypted_str_handles_none() -> None:
    from app.core.crypto import EncryptedStr

    t = EncryptedStr()
    assert t.process_bind_param(None, None) is None
    assert t.process_result_value(None, None) is None


# ── model wiring ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "model_name, cols",
    [
        ("Customer", ["passport_id", "passport_series", "passport_number", "jshshir"]),
        ("Guarantor", ["passport_series", "passport_number", "jshshir"]),
    ],
)
def test_government_id_columns_are_encrypted(model_name: str, cols: list[str]) -> None:
    import app.models as m
    from app.core.crypto import EncryptedStr

    model = getattr(m, model_name)
    for c in cols:
        assert isinstance(model.__table__.c[c].type, EncryptedStr), f"{model_name}.{c} not encrypted"


def test_jshshir_indexes_dropped() -> None:
    from app.models import Customer, Guarantor

    # An index over non-deterministic ciphertext is useless.
    assert not Customer.__table__.c.jshshir.index
    assert not Guarantor.__table__.c.jshshir.index


def test_customer_search_excludes_encrypted_columns() -> None:
    from app.api import tenant as tenant_api

    src = inspect.getsource(tenant_api.list_customers)
    for col in ("passport_series", "passport_number", "passport_id", "jshshir"):
        assert f"Customer.{col}" not in src, f"search still touches encrypted Customer.{col}"


# ── risk hash ────────────────────────────────────────────────────────────────

def test_hash_jshshir_is_hmac_keyed_by_salt() -> None:
    from app.config import settings
    from app.services.risk import hash_jshshir

    js = "12345678901234"
    expected = hmac.new(settings.risk_hash_salt.encode(), js.encode(), hashlib.sha256).hexdigest()
    assert hash_jshshir(js) == expected
    assert hash_jshshir("123") is None  # not a 14-digit id


def test_hash_jshshir_uses_hmac_not_bare_sha256() -> None:
    from app.services.risk import hash_jshshir

    assert "hmac.new(" in inspect.getsource(hash_jshshir)


# ── production config hard-fail ──────────────────────────────────────────────

#: A production `Settings()` now hard-requires a migration credential distinct
#: from the runtime login (`_enforce_database_identity_split`, credential-split
#: cutover) — supply one here so these risk-salt guard tests keep exercising the
#: guard they're named for instead of failing earlier on the identity-split check.
_MIGRATION_URL = "postgresql+psycopg://migration_owner@db/m"


def test_production_refuses_default_risk_salt() -> None:
    from app.config import Settings

    with pytest.raises(ValueError, match="RISK_HASH_SALT"):
        Settings(environment="production", secret_key="A" * 48,
                 risk_hash_salt="muddato-risk-dev-salt",
                 migration_database_url_sync=_MIGRATION_URL)


def test_production_accepts_strong_risk_salt() -> None:
    from app.config import Settings

    s = Settings(environment="production", secret_key="A" * 48,
                 risk_hash_salt="x7-strong-random-salt-value",
                 allowed_origins="https://app.muddato.uz",  # non-localhost (#97)
                 migration_database_url_sync=_MIGRATION_URL)
    assert s.is_production and s.risk_hash_salt == "x7-strong-random-salt-value"
