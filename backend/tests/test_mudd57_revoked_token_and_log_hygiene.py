"""(MUDD-57) Revoked/deactivated tokens must not scope catalog reads, and the
OIDC callback must not spill CSRF state or exception text into the logs.

M-15 — ``catalog._optional_tenant_id`` called ``decode_token`` directly, so a
signature that merely *parses* was enough to bind ``X-Tenant-Id`` to the caller.
Only ``current_user`` checked ``User.is_active`` and the ``tokens_valid_from``
revocation epoch, so a logged-out session and a deactivated (fired) staff account
both kept reading that tenant's *proposed* catalog models.

M-16 — the state-mismatch branch logged both raw state values, and two Redis
error paths logged ``str(e)`` (the Upstash URL carries the password) while their
neighbours in the same file already logged ``type(e).__name__``.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings

# ── M-15: catalog tenant scope must respect revocation ───────────────────────

async def _catalog_world(engine):
    """A tenant with one active member and one tenant-private *proposed* model.

    Returns ``(Session, user_id, tenant_id, brand_id, model_id)``.
    """
    from app.models import CatalogBrand, CatalogModel, Tenant, TenantUser, User

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        user = User(telegram_id=570570)
        tenant = Tenant(slug="mudd57-shop", name="MUDD-57 Shop")
        brand = CatalogBrand(slug="mudd57-brand", name="MUDD-57 Brand", category_ids=[])
        s.add_all([user, tenant, brand])
        await s.flush()
        s.add(TenantUser(tenant_id=tenant.id, user_id=user.id, is_active=True))
        model = CatalogModel(
            brand_id=brand.id, slug="secret-proposal", name="Secret Proposal",
            tenant_id=tenant.id, status="proposed", proposed_by_user_id=user.id,
        )
        s.add(model)
        await s.flush()
        ids = (user.id, tenant.id, brand.id, model.id)
        await s.commit()
    return (Session, *ids)


async def _proposed_visible(client, brand_id, tenant_id, token) -> bool:
    """Does ``token`` get the tenant scope that reveals the proposed model?"""
    r = await client.get(
        f"/api/catalog/brands/{brand_id}/models",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": str(tenant_id)},
    )
    assert r.status_code == 200, f"catalog read should succeed, got {r.status_code}: {r.text[:200]}"
    return any(m["slug"] == "secret-proposal" for m in r.json())


async def test_deactivated_user_token_gets_no_tenant_scope(migrated_db) -> None:
    """A fired employee's still-unexpired token must stop scoping catalog reads."""
    from app.core.security import issue_token
    from app.db import get_db
    from app.main import app
    from app.models import User

    Session, uid, tid, brand_id, _model_id = await _catalog_world(migrated_db)

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    token = issue_token(user_id=str(uid))
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t",
        ) as client:
            # Control: while the account is live the scope works — otherwise a
            # green "deactivated sees nothing" would prove nothing at all.
            assert await _proposed_visible(client, brand_id, tid, token), (
                "an ACTIVE member must see their own proposed model — "
                "the negative assertion below is vacuous without this"
            )

            async with Session() as s:
                u = await s.get(User, uid)
                assert u is not None
                u.is_active = False
                await s.commit()

            assert not await _proposed_visible(client, brand_id, tid, token), (
                "a deactivated user's token must NOT scope the catalog to their tenant"
            )
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_logged_out_token_gets_no_tenant_scope(migrated_db) -> None:
    """"Log out everywhere" must reach the catalog reader too, not just ``current_user``."""
    from app.core.security import issue_token
    from app.db import get_db
    from app.main import app
    from app.models import User

    Session, uid, tid, brand_id, _model_id = await _catalog_world(migrated_db)

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    # Issued an hour ago, so its `iat` is strictly below the revocation epoch we
    # set below — the gate is integer-second, so a same-second token would survive.
    old = issue_token(
        user_id=str(uid),
        extra={"iat": int((datetime.now(UTC) - timedelta(hours=1)).timestamp())},
    )
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t",
        ) as client:
            assert await _proposed_visible(client, brand_id, tid, old), (
                "before logout the token must still scope — control for the assertion below"
            )

            async with Session() as s:
                u = await s.get(User, uid)
                assert u is not None
                u.tokens_valid_from = datetime.now(UTC)
                await s.commit()

            assert not await _proposed_visible(client, brand_id, tid, old), (
                "a token issued before the revocation epoch must NOT scope the catalog"
            )
    finally:
        app.dependency_overrides.pop(get_db, None)


# ── LOW: logout must clear the CSRF cookie too ───────────────────────────────

async def test_logout_clears_both_session_and_csrf_cookies(migrated_db) -> None:
    """Logout cleared ``muddato.session`` and left ``muddato.csrf`` behind."""
    from app.core.security import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME, issue_token
    from app.db import get_db
    from app.main import app
    from app.models import User

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        u = User(telegram_id=571571)
        s.add(u)
        await s.flush()
        uid = u.id
        await s.commit()

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    token = issue_token(user_id=str(uid))
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t",
        ) as client:
            r = await client.delete(
                "/api/auth/logout", headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 200, r.text[:200]
            cleared = {
                c.split("=", 1)[0]
                for c in r.headers.get_list("set-cookie")
                if "Max-Age=0" in c or 'expires=Thu, 01 Jan 1970' in c.lower()
            }
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert SESSION_COOKIE_NAME in cleared, f"logout must clear the session cookie, cleared={cleared}"
    assert CSRF_COOKIE_NAME in cleared, (
        f"logout must clear the double-submit CSRF cookie too, cleared={cleared}"
    )


# ── M-16: no CSRF state / exception text in the OIDC logs ────────────────────

@pytest.fixture(autouse=True)
def _oidc_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_login_client_id", "test-client-id", raising=False)
    monkeypatch.setattr(settings, "telegram_login_client_secret", "test-client-secret", raising=False)


@pytest.fixture(autouse=True)
def _allow_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the fail-closed login limiter a working Redis stub, so these tests
    reach the handler branches instead of short-circuiting to 503."""
    class _LimiterRedis:
        async def eval(self, *_a: object, **_k: object) -> int:
            return 1
    monkeypatch.setattr("app.core.ratelimit.get_redis", lambda: _LimiterRedis())


async def test_state_mismatch_does_not_log_either_state_value(
    async_client, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """The mismatch branch does NOT delete the Redis entry, so the state it logs
    is still live — anyone who can read the logs can replay it."""
    cookie_state = "COOKIESTATEmudd57aaaaaaaa"
    payload_state = "PAYLOADSTATEmudd57bbbbbb"

    async_client.cookies.set("muddato.oidc_state", cookie_state)
    with caplog.at_level(logging.WARNING, logger="muddato.auth.oidc"):
        r = await async_client.post(
            "/api/auth/telegram/callback",
            json={"code": "test_code", "state": payload_state},
        )
    assert r.status_code == 400
    blob = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "callback_state_mismatch" in blob, f"the branch must still be observable, got: {blob!r}"
    assert cookie_state not in blob, f"raw cookie state leaked into the log: {blob!r}"
    assert payload_state not in blob, f"raw payload state leaked into the log: {blob!r}"


@pytest.mark.parametrize(
    ("path", "body", "marker"),
    [
        ("/api/auth/telegram/start", None, "start_redis_unavailable"),
        ("/api/auth/telegram/callback", {"code": "c", "state": "S"}, "callback_redis_unavailable"),
    ],
)
async def test_redis_outage_logs_exception_type_not_message(
    async_client, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    path: str, body: dict | None, marker: str,
) -> None:
    """An Upstash URL carries its password in the connection string, so the
    exception text is credential material — log the type, like the neighbouring
    ``jwks_unreachable`` / ``token_exchange_unreachable`` branches already do."""
    secret = "rediss://default:SUPERSECRETPW@upstash.example:6379"

    class _Boom(Exception):
        pass

    def _exploding_redis():
        rds = AsyncMock()
        rds.setex.side_effect = _Boom(f"connection refused: {secret}")
        rds.get.side_effect = _Boom(f"connection refused: {secret}")
        return rds

    monkeypatch.setattr("app.api.auth_oidc.get_redis", _exploding_redis)
    if body is not None:
        async_client.cookies.set("muddato.oidc_state", body["state"])

    with caplog.at_level(logging.WARNING, logger="muddato.auth.oidc"):
        r = await async_client.post(path, json=body)

    assert r.status_code == 503, f"a Redis outage must fail clean, got {r.status_code}: {r.text[:200]}"
    blob = "\n".join(rec.getMessage() for rec in caplog.records)
    assert marker in blob, f"the branch must still be observable, got: {blob!r}"
    assert "SUPERSECRETPW" not in blob, f"Redis credential leaked into the log: {blob!r}"
    assert "_Boom" in blob, f"expected the exception TYPE in the log, got: {blob!r}"
