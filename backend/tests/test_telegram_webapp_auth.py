"""#59 — Mini-App initData verification falls back to per-tenant bot tokens.

A tenant who configures their OWN @BotFather bot (tenant_settings.bot_token) opens
the same WebApp, but Telegram signs the initData HMAC with the TENANT's bot token —
not the platform token. The login endpoint must verify against the platform token
first and then fall back to the configured tenant bot tokens, otherwise every
tenant-bot user hits 'telegram_initdata_invalid' (the #59 'something went wrong').
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from sqlalchemy.ext.asyncio import async_sessionmaker


def _make_initdata(bot_token: str, *, user_id: int = 12345) -> str:
    """Build a valid Telegram WebApp initData string signed with ``bot_token``."""
    fields = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": user_id, "first_name": "T"}),
        "query_id": "AAA",
    }
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


async def test_initdata_falls_back_to_tenant_bot_token(migrated_db) -> None:
    """MUDD-25 B1: Tenant bot token fallback is DISABLED to prevent account takeover.
    
    Previously, this test checked that tenant bot tokens were accepted as a fallback
    when the platform token didn't match. This opened a severe vulnerability: anyone
    with a tenant's bot token could craft valid initData with any user_id and login
    as that user (complete account takeover without tenant membership validation).
    
    The fallback is now permanently disabled. The proper fix requires:
    1. Frontend: send tenant hint in deep-link start_param
    2. Backend: validate user is a member of that tenant before issuing JWT
    
    Until those are wired, tenant bot logins will fail (acceptable security trade-off).
    """
    from app.api.auth import _verify_webapp_initdata
    from app.models import Tenant, TenantSettings

    token = "111:tenant-bot-secret-token"
    init = _make_initdata(token)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    # No tenant bot configured → platform token won't match this initData → None.
    async with Session() as s:
        assert await _verify_webapp_initdata(s, init) is None

    # Configure a tenant with that bot token → the fallback is DISABLED (returns empty list).
    # So even with a configured token, verification fails. This is the kill-switch
    # for MUDD-25 B1 account takeover vulnerability.
    async with Session() as s:
        t = Tenant(slug="bot-t", name="Bot T")
        s.add(t)
        await s.flush()
        s.add(TenantSettings(tenant_id=t.id, bot_token_ciphertext=token))
        await s.commit()
    async with Session() as s:
        parsed = await _verify_webapp_initdata(s, init)
    # Fallback is disabled → verification fails even though token is configured
    assert parsed is None


async def test_initdata_rejects_a_bad_hash(migrated_db) -> None:
    from app.api.auth import _verify_webapp_initdata

    init = _make_initdata("999:unknown-token")  # signed by a token nobody has
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        assert await _verify_webapp_initdata(s, init) is None  # no platform/tenant match


# MUDD-25 H1 (blocked-tenant mutation guard), H2 (customer PII masking) and H3
# (OIDC CSRF state binding) placeholder tests previously lived here. They
# referenced fixtures that don't exist in this suite (`user_factory`,
# `tenant_factory`, `customer_factory` — collection errors, not passes) or
# asserted nothing at all (`test_oidc_state_mismatch_rejected` was a bare
# `pass`). They've been replaced with real, endpoint-level regression tests:
# see tests/test_mudd25_h1_blocked_tenant.py, tests/test_mudd25_h2_create_customer_pii_masked.py,
# and the H3 cases in tests/test_auth_oidc_callback.py.
