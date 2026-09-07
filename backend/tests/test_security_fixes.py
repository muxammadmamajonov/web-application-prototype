"""Security-hardening unit tests for the 'lock the doors' batch.

Pure-unit (no DB / no live providers), matching the style of
test_payment_webhooks.py. Full HTTP integration coverage is tracked in #79.

Covers:
  * #65 — bot webhook-management endpoints require a platform admin.
  * #64 — subscription webhooks resolve the platform merchant secret correctly
    and reject a tampered Click signature.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.enums import PlatformRole

# ── #65 bot webhook-management auth guard ──────────────────────────────────

def test_require_platform_admin_allows_super_admin_and_support() -> None:
    from app.core.authz import require_platform_admin

    # Should NOT raise.
    require_platform_admin(SimpleNamespace(platform_role=PlatformRole.super_admin))
    require_platform_admin(SimpleNamespace(platform_role=PlatformRole.support))


def test_require_platform_admin_rejects_non_admins() -> None:
    from fastapi import HTTPException

    from app.core.authz import require_platform_admin

    for role in (None, getattr(PlatformRole, "none", None)):
        with pytest.raises(HTTPException) as ei:
            require_platform_admin(SimpleNamespace(platform_role=role))
        # Both halves are a contract the frontend and operator tooling match on.
        assert ei.value.status_code == 403
        assert ei.value.detail == "platform_admin_only"


# ── #64 subscription webhook secret resolution ─────────────────────────────

# (Batch 5 independent-review correction) `_provider_secret` now takes a session
# and fetches ONE provider object through a narrow capability, because reading
# the whole `billing_providers` setting is not something an unauthenticated
# callback may do. The pure "which key spelling holds the secret" decision moved
# to `_secret_of`, which is what these two tests were ever really about.

def test_provider_secret_returns_none_when_disabled_or_unset() -> None:
    from app.api.webhooks.subscription import _secret_of

    # provider missing entirely
    assert _secret_of(None, "payme") is None
    assert _secret_of({}, "payme") is None
    # present but not enabled
    assert _secret_of({"secret_key": "x", "enabled": False}, "payme") is None
    # enabled but no secret
    assert _secret_of({"enabled": True, "merchant_id": "m"}, "payme") is None


def test_provider_secret_reads_common_key_spellings() -> None:
    from app.api.webhooks.subscription import _secret_of

    assert _secret_of({"enabled": True, "secret_key": "s1"}, "payme") == "s1"
    assert _secret_of({"enabled": True, "click_secret_key": "s2"}, "click") == "s2"
    assert _secret_of({"enabled": True, "key": "s3"}, "payme") == "s3"


# ── #64 subscription Click signature verification ──────────────────────────

def test_subscription_click_sign_matches_and_rejects_tamper() -> None:
    """The subscription Click handler recomputes the MD5 with the SAME formula
    as the per-tenant webhook (_click_sign). A correct signature matches; any
    tampered field fails the constant-time compare."""
    from app.api.webhooks.click import _click_sign

    secret = "platform-click-secret"
    base = dict(
        click_trans_id="999",
        service_id="svc-1",
        secret_key=secret,
        merchant_trans_id="topup-uuid",
        amount="50000.00",
        action="1",
        sign_time="2026-06-04 12:00:00",
        merchant_prepare_id="7",
    )
    expected = _click_sign(**base)
    # Same inputs → identical signature (what the handler will compare against).
    assert _click_sign(**base) == expected
    # Tampered amount (e.g. attacker inflates the credit) → different signature.
    tampered = {**base, "amount": "99999999.00"}
    assert _click_sign(**tampered) != expected
