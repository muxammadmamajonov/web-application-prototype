"""#84 — the Payme webhook tenant lookup is an O(1) blind-index probe, not a scan.

DB-free: blind_index is deterministic / keyed / domain-separated; the model has
the indexed column; and the webhook queries by the blind index with no per-row
decrypt loop.
"""
from __future__ import annotations

import inspect


def test_blind_index_is_deterministic_keyed_and_domain_separated() -> None:
    from app.core.crypto import blind_index

    a = blind_index("super-secret", context="payme_secret")
    assert a == blind_index("super-secret", context="payme_secret")   # deterministic
    assert a != blind_index("super-secret", context="other")          # domain-separated
    assert a != blind_index("different-secret", context="payme_secret")  # value-bound
    assert len(a) == 64 and all(c in "0123456789abcdef" for c in a)   # sha256 hex


def test_payme_settings_has_indexed_blind_index_column() -> None:
    from app.models import TenantSettings

    assert TenantSettings.__table__.c.payme_secret_bidx.index is True


def test_payme_lookup_uses_index_not_full_scan() -> None:
    """The O(1) lookup survived the authority reordering.

    (Batch 5 independent-review correction) The lookup moved out of the handler
    and into ``core.webhook_identity.resolve_payme_provider``, which runs it
    through a narrow capability under an authority that holds no table
    privilege — but the SHAPE has to stay the same. A full-table scan that
    Fernet-decrypts every tenant's key is an amplification-DoS vector on an
    unauthenticated endpoint (#84), and moving the query somewhere safer would
    not have made a scan acceptable.
    """
    from app.core.webhook_identity import resolve_payme_provider

    src = inspect.getsource(resolve_payme_provider)
    assert "payme_secret_bidx ==" in src   # indexed equality lookup, ORM path
    assert "rls_webhook_payme_provider" in src  # the capability, RLS path
    assert "blind_index(" in src
    assert "for s in rows" not in src      # no per-row decrypt loop


def test_payme_capability_function_is_an_indexed_single_row_lookup() -> None:
    """And the SQL side of it, read out of the migration that defines it."""
    from pathlib import Path

    body = (
        Path(__file__).resolve().parents[1] / "alembic" / "versions"
        / "rg0credsplit_runtime_credential_split.py"
    ).read_text()
    fn = body.split("rls_webhook_payme_provider(p_secret_bidx text)", 1)[1]
    fn = fn.split("$fn$", 2)[1]
    assert "ts.payme_secret_bidx = p_secret_bidx" in fn
    assert "LIMIT 1" in fn
