"""(#858) Full Payme JSON-RPC protocol for the platform-subscription webhook.

Verifies the transaction state machine on TenantTopup: CheckPerform / Create (idempotent) /
Perform (credits once) / Cancel (reverses a performed credit) / CheckTransaction, the Payme
error codes, and the endpoint auth/dispatch. The provider stays dormant in production until a
platform secret is configured; these tests exercise the handlers + endpoint directly.
"""
from __future__ import annotations

import base64
from decimal import Decimal
from uuid import uuid4

import httpx
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.webhooks.payme import (
    ERR_ACCOUNT_INVALID,
    ERR_AMOUNT_INVALID,
    ERR_AUTH,
    ERR_CANT_PERFORM,
    ERR_METHOD_NOT_FOUND,
    ERR_TRANSACTION_NOT_FOUND,
    STATE_CANCELLED,
    STATE_CANCELLED_AFTER_COMPLETE,
    STATE_COMPLETED,
    STATE_CREATED,
)

_AMOUNT = 50_000          # so'm
_TIYIN = _AMOUNT * 100    # Payme amount (tiyin)


async def _seed_topup(Session, *, amount=_AMOUNT, status="pending"):
    from app.models import Tenant, TenantTopup
    from app.models.enums import TenantStatus

    async with Session() as s:
        t = Tenant(slug=f"p858-{uuid4().hex[:8]}", name="P858", plan_code="trial",
                   status=TenantStatus.active, balance=Decimal(0))
        s.add(t)
        await s.flush()
        tp = TenantTopup(tenant_id=t.id, amount=Decimal(amount), method="payme", status=status)
        s.add(tp)
        await s.flush()
        ids = (t.id, tp.id)
        await s.commit()
    return ids


async def _call(Session, handler, params, rpc_id="rpc-1"):
    async with Session() as s:
        return await handler(s, rpc_id, params)


async def _balance(Session, tid) -> int:
    from app.models import Tenant

    async with Session() as s:
        return int((await s.execute(select(Tenant.balance).where(Tenant.id == tid))).scalar_one())


async def test_full_lifecycle_create_perform_cancel(migrated_db) -> None:
    from app.api.webhooks.subscription import (
        _sub_cancel,
        _sub_check_perform,
        _sub_create,
        _sub_perform,
    )

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    tid, topup_id = await _seed_topup(Session)
    acc = {"topup_id": str(topup_id)}

    # CheckPerformTransaction → allowed.
    r = await _call(Session, _sub_check_perform, {"account": acc, "amount": _TIYIN})
    assert r["result"] == {"allow": True}

    # CreateTransaction → state 1, mapped to our top-up id, idempotent on the tx id.
    r = await _call(Session, _sub_create, {"account": acc, "id": "TX1", "amount": _TIYIN, "time": 111})
    assert r["result"]["state"] == STATE_CREATED
    assert r["result"]["transaction"] == str(topup_id)
    r2 = await _call(Session, _sub_create, {"account": acc, "id": "TX1", "amount": _TIYIN})
    assert r2["result"]["state"] == STATE_CREATED and r2["result"]["transaction"] == str(topup_id)
    assert await _balance(Session, tid) == 0, "Create must not move money"

    # PerformTransaction → credits exactly once; idempotent retry returns the same perform_time.
    r = await _call(Session, _sub_perform, {"id": "TX1"})
    assert r["result"]["state"] == STATE_COMPLETED
    perform_time = r["result"]["perform_time"]
    assert await _balance(Session, tid) == _AMOUNT, "Perform must credit the balance"
    r = await _call(Session, _sub_perform, {"id": "TX1"})
    assert r["result"]["state"] == STATE_COMPLETED and r["result"]["perform_time"] == perform_time
    assert await _balance(Session, tid) == _AMOUNT, "a retried Perform must not double-credit"

    # CancelTransaction (after perform) → reverses the credit; state -2; idempotent.
    r = await _call(Session, _sub_cancel, {"id": "TX1", "reason": 5})
    assert r["result"]["state"] == STATE_CANCELLED_AFTER_COMPLETE
    cancel_time = r["result"]["cancel_time"]
    assert await _balance(Session, tid) == 0, "Cancel of a performed tx must reverse the credit"
    r = await _call(Session, _sub_cancel, {"id": "TX1"})
    assert r["result"]["state"] == STATE_CANCELLED_AFTER_COMPLETE and r["result"]["cancel_time"] == cancel_time
    assert await _balance(Session, tid) == 0, "a retried Cancel must not double-reverse"


async def test_cancel_before_perform_is_state_minus_one_no_money(migrated_db) -> None:
    from app.api.webhooks.subscription import _sub_cancel, _sub_create

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    tid, topup_id = await _seed_topup(Session)
    await _call(Session, _sub_create, {"account": {"topup_id": str(topup_id)}, "id": "TX2", "amount": _TIYIN})
    r = await _call(Session, _sub_cancel, {"id": "TX2", "reason": 3})
    assert r["result"]["state"] == STATE_CANCELLED
    assert await _balance(Session, tid) == 0


async def test_amount_must_match_exactly(migrated_db) -> None:
    from app.api.webhooks.subscription import _sub_check_perform, _sub_create

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    _, topup_id = await _seed_topup(Session)
    acc = {"topup_id": str(topup_id)}
    assert (await _call(Session, _sub_check_perform, {"account": acc, "amount": _TIYIN + 1}))["error"]["code"] == ERR_AMOUNT_INVALID
    assert (await _call(Session, _sub_create, {"account": acc, "id": "TXbad", "amount": _TIYIN - 1}))["error"]["code"] == ERR_AMOUNT_INVALID


async def test_unknown_account_and_transaction_errors(migrated_db) -> None:
    from app.api.webhooks.subscription import _sub_check_perform, _sub_perform

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    bad_acc = {"topup_id": str(uuid4())}
    assert (await _call(Session, _sub_check_perform, {"account": bad_acc, "amount": _TIYIN}))["error"]["code"] == ERR_ACCOUNT_INVALID
    assert (await _call(Session, _sub_check_perform, {"account": {}, "amount": _TIYIN}))["error"]["code"] == ERR_ACCOUNT_INVALID
    assert (await _call(Session, _sub_perform, {"id": "does-not-exist"}))["error"]["code"] == ERR_TRANSACTION_NOT_FOUND


async def test_create_on_already_paid_topup_is_cant_perform(migrated_db) -> None:
    from app.api.webhooks.subscription import _sub_create

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    _, topup_id = await _seed_topup(Session, status="paid")
    r = await _call(Session, _sub_create, {"account": {"topup_id": str(topup_id)}, "id": "TXx", "amount": _TIYIN})
    assert r["error"]["code"] == ERR_CANT_PERFORM


# ── endpoint: auth + dispatch ────────────────────────────────────────────────

async def _client_with_provider(migrated_db, *, secret: str | None):
    """An ASGI client wired to migrated_db, optionally with a Payme platform secret set."""
    from app.db import get_db
    from app.main import app
    from app.models import PlatformSetting

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    if secret is not None:
        async with Session() as s:
            s.add(PlatformSetting(key="billing_providers",
                                  value={"payme": {"enabled": True, "secret_key": secret}}))
            await s.commit()

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    return app, Session


def _basic(secret: str) -> str:
    return "Basic " + base64.b64encode(f"Paycom:{secret}".encode()).decode()


async def test_endpoint_not_configured_returns_503(migrated_db) -> None:
    from app.db import get_db
    from app.main import app

    app_, _ = await _client_with_provider(migrated_db, secret=None)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app_), base_url="http://t") as c:
            r = await c.post("/api/webhooks/subscription/payme", json={"method": "CheckPerformTransaction", "id": 1})
        assert r.status_code == 503
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_endpoint_auth_and_dispatch(migrated_db) -> None:
    from app.db import get_db
    from app.main import app

    app_, Session = await _client_with_provider(migrated_db, secret="S3CR3T")
    _, topup_id = await _seed_topup(Session)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app_), base_url="http://t") as c:
            # bad secret → JSON-RPC auth error (HTTP 200).
            r = await c.post("/api/webhooks/subscription/payme",
                             headers={"Authorization": _basic("WRONG")},
                             json={"method": "CheckPerformTransaction", "id": 1,
                                   "params": {"account": {"topup_id": str(topup_id)}, "amount": _TIYIN}})
            assert r.json()["error"]["code"] == ERR_AUTH
            # good secret + CheckPerform → allow.
            r = await c.post("/api/webhooks/subscription/payme",
                             headers={"Authorization": _basic("S3CR3T")},
                             json={"method": "CheckPerformTransaction", "id": 2,
                                   "params": {"account": {"topup_id": str(topup_id)}, "amount": _TIYIN}})
            assert r.json()["result"] == {"allow": True}
            # unknown method → method-not-found.
            r = await c.post("/api/webhooks/subscription/payme",
                             headers={"Authorization": _basic("S3CR3T")},
                             json={"method": "Nonsense", "id": 3})
            assert r.json()["error"]["code"] == ERR_METHOD_NOT_FOUND
    finally:
        app.dependency_overrides.pop(get_db, None)
