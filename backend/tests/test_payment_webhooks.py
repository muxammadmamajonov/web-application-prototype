"""Round 20 — Click + Payme webhook smoke tests.

We don't spin up the real Payme/Click providers in CI. These tests exercise
the pure pieces:
  * Click signature compute is stable + matches the Click formula
  * Payme Basic auth parser handles malformed/missing headers safely
  * Payme JSON-RPC error/result shape builder

Full happy-path coverage happens in staging by manually posting Click sandbox
forms + Payme JSON-RPC bodies against the deployed webhook URLs.
"""
from __future__ import annotations

import asyncio

from app.api.webhooks.click import _click_sign
from app.api.webhooks.payme import (
    ERR_AUTH,
    ERR_TRANSACTION_NOT_FOUND,
    STATE_COMPLETED,
    _cancel_transaction,
    _check_transaction,
    _now_ms,
    _parse_basic,
    _perform_transaction,
    _rpc_error,
    _rpc_result,
)
from app.models.enums import PaymentStatus

# ── Click ────────────────────────────────────────────────────────────────

def test_click_sign_stable() -> None:
    """The same inputs produce the same MD5. Regression guard against any
    accidental order-of-fields shuffle."""
    a = _click_sign(
        click_trans_id="12345",
        service_id="svc",
        secret_key="topsecret",
        merchant_trans_id="payment-uuid-here",
        amount="10000.00",
        action="0",
        sign_time="2026-05-29 10:00:00",
    )
    b = _click_sign(
        click_trans_id="12345",
        service_id="svc",
        secret_key="topsecret",
        merchant_trans_id="payment-uuid-here",
        amount="10000.00",
        action="0",
        sign_time="2026-05-29 10:00:00",
    )
    assert a == b
    assert len(a) == 32  # md5 hex


def test_click_sign_includes_merchant_prepare_id_when_provided() -> None:
    """Stage-2 (complete) signs differ from stage-1 (prepare) because the
    prepare id is part of the digest."""
    prepare = _click_sign(
        click_trans_id="12345", service_id="svc", secret_key="k",
        merchant_trans_id="mtid", amount="1.00", action="0",
        sign_time="t",
    )
    complete = _click_sign(
        click_trans_id="12345", service_id="svc", secret_key="k",
        merchant_trans_id="mtid", merchant_prepare_id="prep-id",
        amount="1.00", action="1", sign_time="t",
    )
    assert prepare != complete


def test_click_sign_sensitive_to_secret() -> None:
    a = _click_sign(
        click_trans_id="x", service_id="s", secret_key="A",
        merchant_trans_id="m", amount="1", action="0", sign_time="t",
    )
    b = _click_sign(
        click_trans_id="x", service_id="s", secret_key="B",
        merchant_trans_id="m", amount="1", action="0", sign_time="t",
    )
    assert a != b


# ── Payme ────────────────────────────────────────────────────────────────

def test_payme_basic_parser_happy_path() -> None:
    # base64("Paycom:abc123") = "UGF5Y29tOmFiYzEyMw=="
    assert _parse_basic("Basic UGF5Y29tOmFiYzEyMw==") == "abc123"


def test_payme_basic_parser_rejects_missing_or_malformed() -> None:
    assert _parse_basic(None) is None
    assert _parse_basic("") is None
    assert _parse_basic("Bearer xyz") is None
    assert _parse_basic("Basic !!!not-base64") is None
    # base64("nope:abc") = "bm9wZTphYmM="
    assert _parse_basic("Basic bm9wZTphYmM=") is None  # wrong username
    # base64("Paycom") with no colon
    assert _parse_basic("Basic UGF5Y29t") is None


def test_payme_rpc_error_shape() -> None:
    out = _rpc_error(42, ERR_AUTH, "auth_failed")
    assert out == {
        "jsonrpc": "2.0",
        "id": 42,
        "error": {"code": ERR_AUTH, "message": "auth_failed"},
    }


def test_payme_rpc_result_shape() -> None:
    out = _rpc_result("abc", {"allow": True})
    assert out == {"jsonrpc": "2.0", "id": "abc", "result": {"allow": True}}


# ── Payme tenant isolation (C2 — cross-tenant payment IDOR) ───────────────

class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Result:
    def __init__(self, val):
        self._val = val

    def scalar_one_or_none(self):
        return self._val


class _FakeDB:
    """Minimal async stand-in: every execute() yields the same preloaded row."""

    def __init__(self, val):
        self._val = val

    async def execute(self, *_a, **_k):
        return _Result(self._val)


def _cross_tenant_payment():
    """A payment owned by tenant B; the request authenticates as tenant A."""
    ts = _Obj(tenant_id="tenant-A")
    pay = _Obj(tenant_id="tenant-B", external_ref="tx-1", id="pay-1")
    return ts, _FakeDB(pay)


def test_payme_perform_rejects_cross_tenant() -> None:
    ts, db = _cross_tenant_payment()
    res = asyncio.run(_perform_transaction(db, ts, "rpc-1", {"id": "tx-1"}))
    assert res["error"]["code"] == ERR_TRANSACTION_NOT_FOUND


def test_payme_cancel_rejects_cross_tenant() -> None:
    ts, db = _cross_tenant_payment()
    res = asyncio.run(_cancel_transaction(db, ts, "rpc-1", {"id": "tx-1"}))
    assert res["error"]["code"] == ERR_TRANSACTION_NOT_FOUND


def test_payme_check_rejects_cross_tenant() -> None:
    ts, db = _cross_tenant_payment()
    res = asyncio.run(_check_transaction(db, ts, "rpc-1", {"id": "tx-1"}))
    assert res["error"]["code"] == ERR_TRANSACTION_NOT_FOUND


# ── C5 — idempotency / double-credit guard ────────────────────────────────

def test_payme_perform_idempotent_when_already_approved() -> None:
    """An already-approved payment must short-circuit (no second counter bump)
    rather than re-applying. Combined with row-locking this is what prevents
    Payme's aggressive retries from double-crediting an installment."""
    import datetime as _dt

    ts = _Obj(tenant_id="tenant-A")
    pay = _Obj(
        tenant_id="tenant-A", external_ref="tx-1", id="pay-1",
        status=PaymentStatus.approved,
        paid_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.UTC),
    )
    res = asyncio.run(_perform_transaction(_FakeDB(pay), ts, "rpc-1", {"id": "tx-1"}))
    assert "error" not in res
    assert res["result"]["state"] == STATE_COMPLETED


def test_payment_model_has_partial_unique_external_ref_index() -> None:
    """DB-level idempotency guard (R57): unique among non-NULL external_ref."""
    from app.models import Payment

    idx = {ix.name: ix for ix in Payment.__table__.indexes}
    assert "uq_payments_external_ref" in idx
    assert idx["uq_payments_external_ref"].unique is True


def test_now_ms_returns_milliseconds() -> None:
    # Sanity: should be in the 10^12 range (year 2001+).
    assert _now_ms() > 1_500_000_000_000
