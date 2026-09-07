"""F-03 + F-10 — subscription refund-wedge and refused-credit hardening.

F-03 (Payme CancelTransaction): cancelling an already-``paid`` top-up whose funds
were already spent settling invoices used to drive ``tenant.balance`` negative,
violating the ``balance >= 0`` / ``balance_after >= 0`` CHECK constraints. The
uncaught ``IntegrityError`` became a 500, and Payme — which retries cancels hard —
looped forever while the platform kept refundable money. The fix clamps the balance
at zero, books the un-coverable remainder as an explicit receivable ledger entry, and
wraps the mutation+commit so any failure returns a proper JSON-RPC error (not a 500).
It stays idempotent: a re-sent cancel must not double-reverse.

F-10 (Click subscription callback): the handler used to ignore the credit helper's
result and always reply ``{"error": 0}`` (Success), so a short/refused payment told
Click to stop retrying while the platform held uncredited money. The fix returns a
non-zero Click error on genuine refusal, and keeps ``error: 0`` for the idempotent
already-credited case.

The DB-backed tests use the ``migrated_db`` fixture (real schema → the balance CHECK
constraints actually exist) and self-skip without a reachable test Postgres, mirroring
the existing subscription suites. Pure-logic assertions live in the no-DB tests.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import httpx
from httpx import ASGITransport
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.webhooks.click import ERR_BAD_AMOUNT as CLICK_ERR_BAD_AMOUNT
from app.api.webhooks.click import ERR_OK as CLICK_ERR_OK
from app.api.webhooks.payme import (
    ERR_CANT_CANCEL,
    STATE_CANCELLED_AFTER_COMPLETE,
)
from app.api.webhooks.subscription import (
    CREDIT_ALREADY,
    CREDIT_OK,
    CREDIT_REFUSED,
)

_AMOUNT = 100_000          # so'm
_TIYIN = _AMOUNT * 100     # Payme amount (tiyin)


# ── no-DB sanity: the Click error contract the handler relies on ──────────────

def test_click_error_constants_are_distinct() -> None:
    """A refused credit must map to a NON-zero Click code; success stays 0. If these
    ever collapse, F-10's "tell Click to keep retrying" guarantee silently breaks."""
    assert CLICK_ERR_OK == 0
    assert CLICK_ERR_BAD_AMOUNT != 0
    # The three credit outcomes are distinct sentinels.
    assert len({CREDIT_OK, CREDIT_ALREADY, CREDIT_REFUSED}) == 3


# ── shared DB helpers (match the #857/#858 suites' style) ─────────────────────

async def _seed_topup(Session, *, amount=_AMOUNT, status="pending", provider_ref=None):
    from app.models import Tenant, TenantTopup
    from app.models.enums import TenantStatus

    async with Session() as s:
        t = Tenant(slug=f"f03-{uuid4().hex[:8]}", name="F03", plan_code="trial",
                   status=TenantStatus.active, balance=Decimal(0))
        s.add(t)
        await s.flush()
        tp = TenantTopup(tenant_id=t.id, amount=Decimal(amount), method="payme",
                         status=status, provider_ref=provider_ref)
        s.add(tp)
        await s.flush()
        ids = (t.id, tp.id)
        await s.commit()
    return ids


async def _set_balance(Session, tid, value) -> None:
    """Force the tenant balance to a fixed amount (simulating that the topped-up
    funds were already spent settling invoices)."""
    from app.models import Tenant

    async with Session() as s:
        t = await s.get(Tenant, tid)
        t.balance = Decimal(value)
        await s.commit()


async def _balance(Session, tid) -> Decimal:
    from app.models import Tenant

    async with Session() as s:
        return (await s.execute(select(Tenant.balance).where(Tenant.id == tid))).scalar_one()


async def _txns(Session, tid):
    """All ledger entries for the tenant, oldest first, as (kind, amount, balance_after)."""
    from app.models import TenantBalanceTxn

    async with Session() as s:
        rows = (await s.execute(
            select(TenantBalanceTxn.kind, TenantBalanceTxn.amount, TenantBalanceTxn.balance_after)
            .where(TenantBalanceTxn.tenant_id == tid)
            .order_by(TenantBalanceTxn.created_at, TenantBalanceTxn.id)
        )).all()
    return [(k, a, b) for (k, a, b) in rows]


async def _call(Session, handler, params, rpc_id="rpc-1"):
    async with Session() as s:
        return await handler(s, rpc_id, params)


# ── F-03: Payme cancel of spent funds clamps + books a shortfall, no 500 ──────

async def test_cancel_of_spent_topup_clamps_and_books_shortfall(migrated_db) -> None:
    """The headline F-03 case: the top-up is paid, but its funds were already spent
    (balance sits BELOW topup.amount). The cancel must NOT raise on the balance CHECK —
    it clamps the balance at zero, books the un-coverable remainder as a receivable, and
    returns a proper JSON-RPC result (state -2)."""
    from app.api.webhooks.subscription import _sub_cancel

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    tid, _topup_id = await _seed_topup(Session, status="paid", provider_ref="TXspent")
    # Funds already spent settling invoices: only 30k of the 100k top-up remains.
    await _set_balance(Session, tid, 30_000)

    r = await _call(Session, _sub_cancel, {"id": "TXspent", "reason": 5})

    # Proper RPC *result* (not an error, not a raised 500).
    assert "error" not in r, f"cancel must not error, got {r}"
    assert r["result"]["state"] == STATE_CANCELLED_AFTER_COMPLETE

    # Balance clamped at zero (the CHECK would have rejected -70000).
    assert await _balance(Session, tid) == Decimal("0")

    # Two ledger entries: the coverable reversal (-30000) and the receivable shortfall
    # (-70000), both with balance_after == 0 (≥ 0 satisfies the CHECK).
    txns = await _txns(Session, tid)
    kinds = {k for (k, _a, _b) in txns}
    assert "topup_reversal" in kinds and "topup_reversal_shortfall" in kinds, txns
    by_kind = {k: (a, b) for (k, a, b) in txns}
    assert by_kind["topup_reversal"] == (Decimal("-30000"), Decimal("0"))
    assert by_kind["topup_reversal_shortfall"] == (Decimal("-70000"), Decimal("0"))
    # The reversal + shortfall together account for the full top-up amount.
    assert -(by_kind["topup_reversal"][0] + by_kind["topup_reversal_shortfall"][0]) == Decimal(_AMOUNT)


async def test_cancel_of_spent_topup_is_idempotent_on_resend(migrated_db) -> None:
    """A re-sent cancel (Payme retry) must not double-reverse: the second call returns
    the recorded state with no new ledger entries and no further balance change."""
    from app.api.webhooks.subscription import _sub_cancel

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    tid, _topup_id = await _seed_topup(Session, status="paid", provider_ref="TXspent2")
    await _set_balance(Session, tid, 30_000)

    r1 = await _call(Session, _sub_cancel, {"id": "TXspent2", "reason": 5})
    assert r1["result"]["state"] == STATE_CANCELLED_AFTER_COMPLETE
    cancel_time = r1["result"]["cancel_time"]
    n_after_first = len(await _txns(Session, tid))

    r2 = await _call(Session, _sub_cancel, {"id": "TXspent2"})
    assert r2["result"]["state"] == STATE_CANCELLED_AFTER_COMPLETE
    assert r2["result"]["cancel_time"] == cancel_time, "idempotent cancel must echo the stored time"
    assert await _balance(Session, tid) == Decimal("0"), "a retried cancel must not move money again"
    assert len(await _txns(Session, tid)) == n_after_first, "a retried cancel must not add ledger rows"


async def test_cancel_with_fully_covered_balance_books_no_shortfall(migrated_db) -> None:
    """Regression guard: when the funds are still on the balance (not spent), the cancel
    reverses the full amount, leaves balance 0, and books NO shortfall entry — preserving
    the pre-existing #858 reversal semantics."""
    from app.api.webhooks.subscription import _sub_cancel

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    tid, _topup_id = await _seed_topup(Session, status="paid", provider_ref="TXfull")
    await _set_balance(Session, tid, _AMOUNT)   # full top-up still present

    r = await _call(Session, _sub_cancel, {"id": "TXfull", "reason": 1})
    assert r["result"]["state"] == STATE_CANCELLED_AFTER_COMPLETE
    assert await _balance(Session, tid) == Decimal("0")

    txns = await _txns(Session, tid)
    assert [(k, a, b) for (k, a, b) in txns] == [("topup_reversal", Decimal(f"-{_AMOUNT}"), Decimal("0"))]
    assert all(k != "topup_reversal_shortfall" for (k, _a, _b) in txns), "no shortfall when fully covered"


async def test_cancel_failure_returns_rpc_error_not_500(monkeypatch, migrated_db) -> None:
    """If the reversal commit fails on a constraint, the handler must roll back and
    return a JSON-RPC ERR_CANT_CANCEL — never let the IntegrityError escape as a 500
    (that is exactly the retry-loop wedge F-03 is about)."""
    import app.api.webhooks.subscription as sub
    from app.api.webhooks.subscription import _sub_cancel

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    tid, _topup_id = await _seed_topup(Session, status="paid", provider_ref="TXboom")
    await _set_balance(Session, tid, _AMOUNT)

    # Force the commit to raise the same error class a CHECK violation would.
    from sqlalchemy.exc import IntegrityError

    async def _boom(self):
        raise IntegrityError("forced", None, Exception("balance_after_nonneg"))

    async def _noop_rollback(self):
        return None

    monkeypatch.setattr(sub.AsyncSession, "commit", _boom, raising=True)
    monkeypatch.setattr(sub.AsyncSession, "rollback", _noop_rollback, raising=True)

    r = await _call(Session, _sub_cancel, {"id": "TXboom", "reason": 5})
    assert "error" in r, f"a failed cancel must surface an RPC error, got {r}"
    assert r["error"]["code"] == ERR_CANT_CANCEL


# ── F-10: Click subscription callback distinguishes refusal from success ──────

def _click_form(secret: str, *, topup_id: str, amount: str, action: str = "1") -> dict:
    """Build a correctly-signed Click Complete form body for the subscription webhook."""
    from app.api.webhooks.click import _click_sign

    click_trans_id = "ct-" + uuid4().hex[:8]
    sign_time = "2026-06-24 10:00:00"
    sign = _click_sign(
        click_trans_id=click_trans_id,
        service_id="svc-1",
        secret_key=secret,
        merchant_trans_id=topup_id,
        merchant_prepare_id="" if action == "1" else None,
        amount=amount,
        action=action,
        sign_time=sign_time,
    )
    return {
        "click_trans_id": click_trans_id,
        "service_id": "svc-1",
        "merchant_trans_id": topup_id,
        "merchant_prepare_id": "",
        "amount": amount,
        "action": action,
        "sign_time": sign_time,
        "sign_string": sign,
    }


async def _click_app(migrated_db, *, secret: str):
    from app.db import get_db
    from app.main import app
    from app.models import PlatformSetting

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        s.add(PlatformSetting(key="billing_providers",
                              value={"click": {"enabled": True, "secret_key": secret}}))
        await s.commit()

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    return app, Session


async def test_click_short_payment_returns_nonzero_error(migrated_db) -> None:
    """A short captured amount is a genuine refusal — Click must be told a non-zero
    error so it retries, and the balance must stay uncredited (no stranded money)."""
    from app.db import get_db
    from app.main import app
    from app.models import TenantTopup

    secret = "CLICKSECRET"
    app_, Session = await _click_app(migrated_db, secret=secret)
    tid, topup_id = await _seed_topup(Session, amount=_AMOUNT)
    try:
        form = _click_form(secret, topup_id=str(topup_id), amount=str(_AMOUNT - 1))  # pays less
        async with httpx.AsyncClient(transport=ASGITransport(app=app_), base_url="http://t") as c:
            r = await c.post("/api/webhooks/subscription/click", data=form)
        body = r.json()
        assert body["error"] == CLICK_ERR_BAD_AMOUNT, f"refused credit must be non-zero, got {body}"
        # Nothing credited, top-up not flipped to paid.
        assert await _balance(Session, tid) == Decimal("0")
        async with Session() as s:
            assert (await s.get(TenantTopup, topup_id)).status == "pending"
        # No spurious audit/ledger row for money that never landed.
        async with Session() as s:
            from app.models import TenantBalanceTxn
            n = (await s.execute(
                select(func.count(TenantBalanceTxn.id)).where(TenantBalanceTxn.tenant_id == tid)
            )).scalar_one()
        assert n == 0
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_click_already_credited_returns_success(migrated_db) -> None:
    """An idempotent redelivery of a top-up that is already paid must return error:0
    (Success) so Click stops retrying — distinct from the refusal case above."""
    from app.db import get_db
    from app.main import app

    secret = "CLICKSECRET"
    app_, Session = await _click_app(migrated_db, secret=secret)
    # Top-up is already paid (a prior delivery credited it); a redelivery arrives.
    tid, topup_id = await _seed_topup(Session, amount=_AMOUNT, status="paid", provider_ref="ct-old")
    try:
        form = _click_form(secret, topup_id=str(topup_id), amount=str(_AMOUNT))
        async with httpx.AsyncClient(transport=ASGITransport(app=app_), base_url="http://t") as c:
            r = await c.post("/api/webhooks/subscription/click", data=form)
        body = r.json()
        assert body["error"] == CLICK_ERR_OK, f"already-credited redelivery must be success, got {body}"
        # Balance unchanged (no double credit) and no new ledger row.
        assert await _balance(Session, tid) == Decimal("0")
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_click_happy_path_credits_and_returns_success(migrated_db) -> None:
    """A correct Complete with a matching amount credits the balance and returns 0."""
    from app.db import get_db
    from app.main import app
    from app.models import TenantTopup

    secret = "CLICKSECRET"
    app_, Session = await _click_app(migrated_db, secret=secret)
    tid, topup_id = await _seed_topup(Session, amount=_AMOUNT)
    try:
        form = _click_form(secret, topup_id=str(topup_id), amount=str(_AMOUNT))
        async with httpx.AsyncClient(transport=ASGITransport(app=app_), base_url="http://t") as c:
            r = await c.post("/api/webhooks/subscription/click", data=form)
        assert r.json()["error"] == CLICK_ERR_OK
        assert await _balance(Session, tid) == Decimal(_AMOUNT)
        async with Session() as s:
            assert (await s.get(TenantTopup, topup_id)).status == "paid"
    finally:
        app.dependency_overrides.pop(get_db, None)


# ── F-10 unit-level: the status classifier the handler keys off ───────────────

async def test_credit_status_refused_for_missing_amount(migrated_db) -> None:
    from app.api.webhooks.subscription import _credit_by_topup_id_status

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    _tid, topup_id = await _seed_topup(Session, amount=_AMOUNT)
    async with Session() as s:
        status = await _credit_by_topup_id_status(
            s, str(topup_id), provider_ref="r", captured_amount=None,
        )
    assert status == CREDIT_REFUSED


async def test_credit_status_refused_for_unknown_topup(migrated_db) -> None:
    from app.api.webhooks.subscription import _credit_by_topup_id_status

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        status = await _credit_by_topup_id_status(
            s, str(uuid4()), provider_ref="r", captured_amount=Decimal(_AMOUNT),
        )
    assert status == CREDIT_REFUSED


async def test_credit_status_already_for_paid_topup(migrated_db) -> None:
    from app.api.webhooks.subscription import _credit_by_topup_id_status

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    _tid, topup_id = await _seed_topup(Session, amount=_AMOUNT, status="paid", provider_ref="ct-paid")
    async with Session() as s:
        status = await _credit_by_topup_id_status(
            s, str(topup_id), provider_ref="r", captured_amount=Decimal(_AMOUNT),
        )
    assert status == CREDIT_ALREADY


async def test_credit_status_ok_then_already_on_retry(migrated_db) -> None:
    """First delivery credits (CREDIT_OK); a later redelivery sees 'paid' (CREDIT_ALREADY).
    Proves the bool wrapper's True/False contract still holds for the existing suites."""
    from app.api.webhooks.subscription import _credit_by_topup_id, _credit_by_topup_id_status

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    tid, topup_id = await _seed_topup(Session, amount=_AMOUNT)
    async with Session() as s:
        first = await _credit_by_topup_id_status(
            s, str(topup_id), provider_ref="ct-1", captured_amount=Decimal(_AMOUNT),
        )
    async with Session() as s:
        retry = await _credit_by_topup_id_status(
            s, str(topup_id), provider_ref="ct-1", captured_amount=Decimal(_AMOUNT),
        )
    assert first == CREDIT_OK and retry == CREDIT_ALREADY
    assert await _balance(Session, tid) == Decimal(_AMOUNT)
    # bool wrapper: True only on the crediting call.
    async with Session() as s:
        again = await _credit_by_topup_id(
            s, str(topup_id), provider_ref="ct-1", captured_amount=Decimal(_AMOUNT),
        )
    assert again is False


# ── endpoint auth: refusal classification only happens after a valid signature ─

async def test_click_bad_signature_is_sign_error_not_credit(migrated_db) -> None:
    from app.db import get_db
    from app.main import app

    secret = "CLICKSECRET"
    app_, Session = await _click_app(migrated_db, secret=secret)
    _tid, topup_id = await _seed_topup(Session, amount=_AMOUNT)
    try:
        form = _click_form(secret, topup_id=str(topup_id), amount=str(_AMOUNT))
        form["sign_string"] = "deadbeef" * 4   # corrupt the signature
        async with httpx.AsyncClient(transport=ASGITransport(app=app_), base_url="http://t") as c:
            r = await c.post("/api/webhooks/subscription/click", data=form)
        assert r.json()["error"] == -1, "bad signature returns SIGN CHECK FAILED, not a credit code"
    finally:
        app.dependency_overrides.pop(get_db, None)
