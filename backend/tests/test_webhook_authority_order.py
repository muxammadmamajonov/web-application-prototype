"""A public webhook proves who it is BEFORE it can reach anything.

The defect these tests pin down: ``worker_authority`` — full cross-tenant reach
— was a router dependency on ``/webhooks/click``, ``/webhooks/payme`` and
``/webhooks/subscription``. So an unauthenticated HTTP request held cross-tenant
database permission from the moment it was routed, and each handler's FIRST act
was a global ``tenant_settings`` lookup performed with it, *before* the provider
signature was checked.

Everything here runs the real application against a real migrated database,
through the RESTRICTED runtime login, with the authority layer ON. That
combination is the point: a webhook test that runs as the schema owner cannot
tell an authorization boundary from its absence.
"""
from __future__ import annotations

import hashlib
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.crypto import blind_index, encrypt_secret
from tests import _dbcreds

pytestmark = pytest.mark.asyncio(loop_scope="module")

CLICK_SERVICE_A = "svc-alpha-1"
CLICK_SECRET_A = "click-secret-alpha"
CLICK_SERVICE_B = "svc-beta-2"
CLICK_SECRET_B = "click-secret-beta"
PAYME_SECRET_A = "payme-secret-alpha"


def _click_sign(**parts: str) -> str:
    order = (
        "click_trans_id", "service_id", "secret_key", "merchant_trans_id",
        "merchant_prepare_id", "amount", "action", "sign_time",
    )
    return hashlib.md5(
        "".join(parts[k] for k in order if k in parts).encode()
    ).hexdigest()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world(rls_world):
    """Two shops with DIFFERENT Click/Payme merchant credentials.

    Seeded on the OWNER connection with raw SQL rather than through a
    ``Session``: the ``after_begin`` listener is registered on ``Session``, so
    going around it is what lets the seed write freely while the application
    under test stays subject to every policy.
    """
    owner = create_async_engine(_dbcreds.migration_url(), poolclass=None)
    async with owner.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tenant_settings SET click_service_id = :sid, "
                "click_secret_key = :sec, payme_secret_key = :psec, "
                "payme_secret_bidx = :pbidx WHERE tenant_id = :t"
            ),
            {
                "sid": CLICK_SERVICE_A, "sec": encrypt_secret(CLICK_SECRET_A),
                "psec": encrypt_secret(PAYME_SECRET_A),
                "pbidx": blind_index(PAYME_SECRET_A, context="payme_secret"),
                "t": rls_world.tenant_a,
            },
        )
        await conn.execute(
            text(
                "UPDATE tenant_settings SET click_service_id = :sid, "
                "click_secret_key = :sec WHERE tenant_id = :t"
            ),
            {
                "sid": CLICK_SERVICE_B, "sec": encrypt_secret(CLICK_SECRET_B),
                "t": rls_world.tenant_b,
            },
        )
    await owner.dispose()
    return rls_world


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client(world, request):
    """The real app, on the restricted login, with the authority layer ON."""
    from app.config import settings as app_settings
    from app.db import get_db, reset_db_scope
    from app.main import app

    url = _dbcreds.runtime_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — the webhook tier needs real Postgres")
    engine = create_async_engine(url, poolclass=None)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _override():
        reset_db_scope()
        async with Session() as session:
            yield session

    was = app_settings.rls_enabled
    app_settings.rls_enabled = True
    app.dependency_overrides[get_db] = _override
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t",
        ) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)
        app_settings.rls_enabled = was
        reset_db_scope()
        await engine.dispose()


def _restore_override(previous):
    """Put back whatever `get_db` override was in place before a test replaced it."""
    from app.db import get_db
    from app.main import app

    if previous is None:
        app.dependency_overrides.pop(get_db, None)
    else:
        app.dependency_overrides[get_db] = previous


def _click_form(**over) -> dict[str, str]:
    body = {
        "click_trans_id": "ct-1", "service_id": CLICK_SERVICE_A,
        "click_paydoc_id": "pd-1", "merchant_trans_id": str(uuid4()),
        "amount": "100.00", "action": "0", "sign_time": "2026-01-01 00:00:00",
        "sign_string": "0" * 32,
    }
    body.update(over)
    return body


# ── the router carries nothing ───────────────────────────────────────────────

def test_no_public_webhook_route_carries_a_router_level_authority():
    """The structural half of the fix, asserted on the assembled application.

    Not a grep: a dependency can be attached at the router, at the route, or via
    an included sub-router, and only the built app knows the answer.
    """
    from fastapi.routing import APIRoute

    from app.main import app

    offenders = []
    checked = 0
    for route in app.routes:
        if not isinstance(route, APIRoute) or "/webhooks/" not in route.path:
            continue
        checked += 1
        names = {
            getattr(d.call, "__name__", "") for d in route.dependant.dependencies
            if d.call is not None
        }
        if names & {"worker_authority", "platform_authority", "_worker_authority"}:
            offenders.append(route.path)
    assert checked >= 3, "the webhook routes were not found — has the prefix changed?"
    assert offenders == [], (
        f"public webhook route(s) hold cross-tenant authority at the door: {offenders}"
    )


# ── unauthenticated requests reach nothing ───────────────────────────────────

async def test_invalid_click_signature_has_no_cross_tenant_access(client, world):
    r = await client.post("/api/webhooks/click/prepare", data=_click_form())
    assert r.status_code == 200
    assert r.json()["error"] == -1, "a bad signature must fail the signature check"


async def test_missing_click_signature_has_no_access(client):
    """Both shapes of "no signature" are refused before any handler logic runs.

    An absent field and an empty one are both rejected by request validation
    (422) — the endpoint body never executes, so there is no window in which an
    unsigned request holds any authority at all. A garbage-but-present signature
    is the case that reaches ``_authenticate``; that one is covered above.
    """
    absent = _click_form()
    del absent["sign_string"]
    assert (await client.post("/api/webhooks/click/prepare", data=absent)).status_code == 422

    blank = _click_form(sign_string="")
    assert (await client.post("/api/webhooks/click/prepare", data=blank)).status_code == 422


async def test_unknown_merchant_and_bad_signature_are_indistinguishable(client):
    """Non-enumerating: the reply must not reveal whether a merchant id exists."""
    unknown = await client.post(
        "/api/webhooks/click/prepare", data=_click_form(service_id="svc-does-not-exist"),
    )
    known_bad_sig = await client.post("/api/webhooks/click/prepare", data=_click_form())
    assert unknown.json()["error"] == known_bad_sig.json()["error"] == -1
    assert unknown.json()["error_note"] == known_bad_sig.json()["error_note"]


async def test_malformed_body_carries_no_authority(client):
    r = await client.post("/api/webhooks/click/prepare", data={"action": "0"})
    assert r.status_code == 422  # rejected by validation, before any handler code


async def test_missing_payme_credential_is_rejected(client):
    r = await client.post(
        "/api/webhooks/payme", json={"id": 1, "method": "CheckTransaction", "params": {}},
    )
    assert r.json()["error"]["code"] == -32504


async def test_wrong_payme_credential_is_rejected(client):
    import base64

    bad = base64.b64encode(b"Paycom:not-the-secret").decode()
    r = await client.post(
        "/api/webhooks/payme",
        json={"id": 1, "method": "CheckTransaction", "params": {"id": "x"}},
        headers={"Authorization": f"Basic {bad}"},
    )
    assert r.json()["error"]["code"] == -32504


# ── a verified request is narrowed to exactly one tenant ─────────────────────

async def test_valid_signature_resolves_exactly_one_tenant(client, world):
    """The resolver returns shop A's tenant for shop A's service_id, and only it."""
    from app.config import settings as app_settings
    from app.core.webhook_identity import resolve_click_provider
    from app.db import reset_db_scope

    engine = create_async_engine(_dbcreds.runtime_url(), poolclass=None)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    was = app_settings.rls_enabled
    app_settings.rls_enabled = True
    try:
        reset_db_scope()
        async with Session() as s:
            a = await resolve_click_provider(s, CLICK_SERVICE_A)
            b = await resolve_click_provider(s, CLICK_SERVICE_B)
            missing = await resolve_click_provider(s, "svc-nope")
        assert a is not None and str(a.tenant_id) == world.tenant_a
        assert b is not None and str(b.tenant_id) == world.tenant_b
        assert missing is None
    finally:
        app_settings.rls_enabled = was
        reset_db_scope()
        await engine.dispose()


async def test_signature_validation_precedes_any_ordinary_cross_tenant_query(client):
    """Order, asserted by observation rather than by reading the code.

    Every statement the request issues is recorded. A request that fails the
    signature check must never have touched an ordinary tenant table — the only
    thing it is allowed to have run is the narrow provider resolver.
    """
    from sqlalchemy import event

    from app.config import settings as app_settings
    from app.db import get_db, reset_db_scope
    from app.main import app

    engine = create_async_engine(_dbcreds.runtime_url(), poolclass=None)
    seen: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _record(conn, cursor, statement, params, context, executemany):
        seen.append(" ".join(statement.split()).lower())

    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _override():
        reset_db_scope()
        async with Session() as session:
            yield session

    was = app_settings.rls_enabled
    app_settings.rls_enabled = True
    outer = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t",
        ) as c:
            await c.post("/api/webhooks/click/prepare", data=_click_form())
    finally:
        # RESTORE, not pop: the module-scoped `client` fixture installed its own
        # override, and popping it would leave every later test in this module
        # talking to the import-time engine — which belongs to a different event
        # loop and fails with "Event loop is closed" a long way from here.
        _restore_override(outer)
        app_settings.rls_enabled = was
        reset_db_scope()
        await engine.dispose()

    forbidden = ("from tenant_settings", "from payments", "from installments")
    offending = [s for s in seen if any(f in s for f in forbidden)]
    assert offending == [], (
        "an unauthenticated webhook queried a tenant table before verifying: "
        f"{offending[:2]}"
    )
    assert any("rls_webhook_click_provider" in s for s in seen), (
        "the narrow resolver was never called — the test is not exercising the path"
    )


async def test_removing_post_verification_scoping_is_caught(client, world):
    """MUTATION: a verified callback that is NOT narrowed reaches nothing.

    ``_authenticate`` pins ``app.tenant_id`` on success. With that removed, the
    handler runs under ``muddato_app`` with no scope — and the fail-closed
    policies mean it finds no payment at all, so the callback stops working.
    That is the safe direction, and this asserts it is the direction taken.
    """
    import app.api.webhooks.click as click_mod
    from app.config import settings as app_settings
    from app.db import get_db, reset_db_scope
    from app.main import app

    engine = create_async_engine(_dbcreds.runtime_url(), poolclass=None)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _override():
        reset_db_scope()
        async with Session() as session:
            yield session

    async def _unscoped_authenticate(db, *, service_id, sign_string, **signed):
        from app.core.crypto import decrypt_secret
        from app.core.webhook_identity import resolve_click_provider

        provider = await resolve_click_provider(db, service_id)
        if provider is None:
            return None
        secret = decrypt_secret(provider.secret_ciphertext)
        expected = click_mod._click_sign(
            service_id=service_id, secret_key=secret or "", **signed,
        )
        if expected != (sign_string or "").lower():
            return None
        return provider  # deliberately does NOT call set_tenant_scope

    original = click_mod._authenticate
    was = app_settings.rls_enabled
    app_settings.rls_enabled = True
    click_mod._authenticate = _unscoped_authenticate
    outer = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override
    try:
        form = _click_form(merchant_trans_id=world.payment_a)
        form["sign_string"] = _click_sign(
            click_trans_id=form["click_trans_id"], service_id=CLICK_SERVICE_A,
            secret_key=CLICK_SECRET_A, merchant_trans_id=form["merchant_trans_id"],
            amount=form["amount"], action=form["action"], sign_time=form["sign_time"],
        )
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t",
        ) as c:
            unscoped = await c.post("/api/webhooks/click/prepare", data=form)
        # -5 = transaction_not_found: the database refused to show it the payment.
        assert unscoped.json()["error"] == -5, (
            "an unscoped verified callback still reached the payment — the "
            "post-verification narrowing is not what is protecting it"
        )

        click_mod._authenticate = original
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t",
        ) as c:
            scoped = await c.post("/api/webhooks/click/prepare", data=form)
        assert scoped.json()["error"] != -5, (
            "with the narrowing restored the callback must find its own payment"
        )
    finally:
        click_mod._authenticate = original
        _restore_override(outer)
        app_settings.rls_enabled = was
        reset_db_scope()
        await engine.dispose()


async def test_valid_callback_cannot_reach_another_tenant(client, world):
    """Shop B's merchant key naming shop A's payment gets nothing.

    Two independent refusals now stand here: the tenant scope pinned from B's
    credential filters A's payment out in SQL, and the handler's own ownership
    comparison would reject it too. Before this correction only the second one
    existed, running under cross-tenant authority.
    """
    form = _click_form(service_id=CLICK_SERVICE_B, merchant_trans_id=world.payment_a)
    form["sign_string"] = _click_sign(
        click_trans_id=form["click_trans_id"], service_id=CLICK_SERVICE_B,
        secret_key=CLICK_SECRET_B, merchant_trans_id=form["merchant_trans_id"],
        amount=form["amount"], action=form["action"], sign_time=form["sign_time"],
    )
    r = await client.post("/api/webhooks/click/prepare", data=form)
    assert r.json()["error"] == -5, "shop B reached shop A's payment"


async def test_amount_and_state_correctness_are_unchanged(client, world):
    """A correctly signed callback for a mismatched amount still fails on AMOUNT.

    The regression this guards against is subtle: if the scoping change had
    quietly stopped the handler from finding its own payment, every one of these
    would return "not found" and the amount checks would look like they passed
    while never running.
    """
    form = _click_form(merchant_trans_id=world.payment_a, amount="999999.00")
    form["sign_string"] = _click_sign(
        click_trans_id=form["click_trans_id"], service_id=CLICK_SERVICE_A,
        secret_key=CLICK_SECRET_A, merchant_trans_id=form["merchant_trans_id"],
        amount=form["amount"], action=form["action"], sign_time=form["sign_time"],
    )
    r = await client.post("/api/webhooks/click/prepare", data=form)
    assert r.json()["error"] == -2, "expected the AMOUNT error, not not-found"


async def test_replay_of_a_valid_prepare_is_idempotent(client, world):
    """Duplicate delivery behaviour is unchanged by the authority reordering."""
    form = _click_form(merchant_trans_id=world.payment_a, amount="100.00")
    form["sign_string"] = _click_sign(
        click_trans_id=form["click_trans_id"], service_id=CLICK_SERVICE_A,
        secret_key=CLICK_SECRET_A, merchant_trans_id=form["merchant_trans_id"],
        amount=form["amount"], action=form["action"], sign_time=form["sign_time"],
    )
    first = await client.post("/api/webhooks/click/prepare", data=form)
    second = await client.post("/api/webhooks/click/prepare", data=form)
    assert first.json()["error"] == second.json()["error"]
    assert first.json().get("merchant_prepare_id") == second.json().get("merchant_prepare_id")


async def test_webhook_authority_holds_no_table_privilege(world):
    """``muddato_webhook`` can call the resolvers and read nothing else.

    This is what makes the pre-verification window safe: even if a future
    handler ran a query before checking a signature, the authority it holds at
    that moment has no grant on any table to run it with.
    """
    engine = create_async_engine(_dbcreds.runtime_url(), poolclass=None)
    try:
        # A fresh transaction per probe: the first refusal aborts the current
        # one, and every statement after that fails with "transaction is
        # aborted" — which would make the remaining probes pass for the wrong
        # reason.
        for table in ("users", "customers", "payments", "tenant_settings"):
            with pytest.raises(Exception) as caught:
                async with engine.begin() as conn:
                    await conn.execute(text("SET LOCAL ROLE muddato_webhook"))
                    await conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
            assert "permission denied" in str(caught.value).lower(), (
                f"muddato_webhook was not refused on {table}"
            )
    finally:
        await engine.dispose()


async def test_ordinary_tenant_authority_cannot_call_the_provider_resolvers(world):
    """A tenant handler must not be able to read a competitor's merchant secret."""
    engine = create_async_engine(_dbcreds.runtime_url(), poolclass=None)
    try:
        for role in ("muddato_app", "muddato_customer", "muddato_worker"):
            async with engine.begin() as conn:
                await conn.execute(text(f"SET LOCAL ROLE {role}"))
                with pytest.raises(Exception) as caught:
                    await conn.execute(text(
                        "SELECT * FROM public.rls_webhook_click_provider('x')",
                    ))
                assert "permission denied" in str(caught.value).lower(), (
                    f"{role} can call the Click provider resolver"
                )
    finally:
        await engine.dispose()


async def test_subscription_billing_secret_is_not_readable_by_a_tenant(world):
    """The platform's own merchant secret is not in the tenant-readable allowlist."""
    engine = create_async_engine(_dbcreds.runtime_url(), poolclass=None)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SET LOCAL ROLE muddato_app"))
            rows = (await conn.execute(text(
                "SELECT 1 FROM platform_settings WHERE key = 'billing_providers'",
            ))).all()
        assert rows == []
    finally:
        await engine.dispose()


def test_click_amount_helper_is_unchanged():
    """A guard on the signature helper itself, so the reordering cannot have
    silently changed what is signed."""
    from app.api.webhooks.click import _click_sign as impl

    assert impl(
        click_trans_id="a", service_id="b", secret_key="c", merchant_trans_id="d",
        amount="1", action="0", sign_time="t",
    ) == hashlib.md5(b"abcd10t").hexdigest()
    assert impl(
        click_trans_id="a", service_id="b", secret_key="c", merchant_trans_id="d",
        merchant_prepare_id="e", amount="1", action="1", sign_time="t",
    ) == hashlib.md5(b"abcde11t").hexdigest()
