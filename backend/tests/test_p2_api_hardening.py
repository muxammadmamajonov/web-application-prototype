"""P2 backend API hardening — #85, #86, #87, #97.

DB-free guards: schema introspection (#85), source inspection (#86, #97), the
IntegrityError handler's constraint→code mapping (#87), and the production config
boot-guard for CORS origins (#97).
"""
from __future__ import annotations

import inspect

import orjson
import pytest

#: A production `Settings()` now hard-requires a migration credential distinct
#: from the runtime login (`_enforce_database_identity_split`, credential-split
#: cutover) — supply one here so these CORS/RLS guard tests keep exercising the
#: guard they're named for instead of failing earlier on the identity-split check.
_MIGRATION_URL = "postgresql+psycopg://migration_owner@db/m"

# ── #85: CustomerPatch can't set status / telegram_id (moderation bypass) ─────

def test_customer_patch_excludes_moderation_fields() -> None:
    from app.api.tenant import CustomerPatch

    fields = CustomerPatch.model_fields
    assert "status" not in fields, "status must change via approve/reject, not PATCH"
    assert "telegram_id" not in fields, "telegram_id is identity, not client-settable"


# ── #86: inline record-and-approve locks the installment ──────────────────────

def test_inline_approve_locks_installment() -> None:
    from app.api.tenant import create_payment

    src = inspect.getsource(create_payment)
    assert "with_for_update()" in src, "inline approve must SELECT ... FOR UPDATE"


# ── #87: IntegrityError → 409 with a stable code ──────────────────────────────

class _FakeOrig(Exception):
    def __init__(self, constraint_name: str) -> None:
        super().__init__(constraint_name)
        self.constraint_name = constraint_name


async def test_integrity_handler_maps_known_constraint() -> None:
    from types import SimpleNamespace

    import app.main as main

    exc = main.IntegrityError("INSERT", {}, _FakeOrig("uq_guarantor_tenant_phone"))
    req = SimpleNamespace(url=SimpleNamespace(path="/api/tenant/guarantors"))
    resp = await main._integrity_error_handler(req, exc)
    assert resp.status_code == 409
    assert orjson.loads(resp.body)["detail"] == "guarantor_phone_exists"


async def test_integrity_handler_unknown_constraint_is_generic_conflict() -> None:
    from types import SimpleNamespace

    import app.main as main

    exc = main.IntegrityError("INSERT", {}, _FakeOrig("uq_something_unmapped"))
    req = SimpleNamespace(url=SimpleNamespace(path="/x"))
    resp = await main._integrity_error_handler(req, exc)
    assert resp.status_code == 409
    assert orjson.loads(resp.body)["detail"] == "conflict"  # 409, never 500/mislabeled


def test_create_guarantor_no_longer_swallows_all_errors() -> None:
    from app.api.tenant import create_guarantor

    src = inspect.getsource(create_guarantor)
    assert "except Exception" not in src, "bare except mislabeled every error as a duplicate"


# ── #97: production refuses wildcard / localhost / empty CORS origins ─────────

@pytest.mark.parametrize(
    "origins",
    ["*", "http://localhost:5173", "https://app.x.com,http://localhost:3000", ""],
)
def test_production_refuses_bad_cors_origins(origins: str) -> None:
    from app.config import Settings

    with pytest.raises(ValueError, match="ALLOWED_ORIGINS"):
        Settings(environment="production", secret_key="A" * 48,
                 risk_hash_salt="a-strong-random-salt", allowed_origins=origins,
                 migration_database_url_sync=_MIGRATION_URL)


def test_production_accepts_explicit_https_origin() -> None:
    from app.config import Settings

    s = Settings(environment="production", secret_key="A" * 48,
                 risk_hash_salt="a-strong-random-salt", allowed_origins="https://app.muddato.uz",
                 migration_database_url_sync=_MIGRATION_URL)
    assert s.allowed_origins_list == ["https://app.muddato.uz"]


def test_cors_middleware_is_not_wildcard() -> None:
    import app.main as main

    src = inspect.getsource(main)
    assert 'allow_methods=["*"]' not in src
    assert 'allow_headers=["*"]' not in src


# ── #124: production warns (CRITICAL) when the RLS backstop is disabled ───────

def test_production_warns_when_rls_disabled(caplog) -> None:
    import logging

    from app.config import Settings

    with caplog.at_level(logging.CRITICAL, logger="muddato.config"):
        Settings(environment="production", secret_key="A" * 48,
                 risk_hash_salt="a-strong-random-salt",
                 allowed_origins="https://app.muddato.uz", rls_enabled=False,
                 migration_database_url_sync=_MIGRATION_URL)
    assert any("RLS_ENABLED" in r.message for r in caplog.records)


def test_production_quiet_when_rls_enabled(caplog) -> None:
    import logging

    from app.config import Settings

    with caplog.at_level(logging.CRITICAL, logger="muddato.config"):
        Settings(environment="production", secret_key="A" * 48,
                 risk_hash_salt="a-strong-random-salt",
                 allowed_origins="https://app.muddato.uz", rls_enabled=True,
                 migration_database_url_sync=_MIGRATION_URL)
    assert not any("RLS_ENABLED" in r.message for r in caplog.records)
