"""(MUDD-59 H6/M-05) `click.py` and `subscription.py` handler-crash + already-paid regressions.

H6 — payme.py's webhook already had the right shape: outer parse try/except, inner
try/except around the handler body that turns any crash into a structured
provider-shaped reply instead of a raw HTTP 500 (which Click/Payme either retry
blindly or park the transaction in an "unknown" state). `click.py`'s
`/prepare` + `/complete` and `subscription.py`'s `/payme` + `/click` had none of
that — this file proves each of the four endpoints now mirrors the pattern.

M-05 — Click's `/complete` re-delivering for an already-approved Payment used to
fall through to the same `ERR_OK` reply as a brand-new approval, so a retry was
indistinguishable from success. It must now answer `ERR_ALREADY_PAID` (-4).
"""
from __future__ import annotations

import base64
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.api.webhooks.click import (
    ERR_ALREADY_PAID,
    ERR_INTERNAL,
    ERR_OK,
    complete,
    prepare,
)
from app.api.webhooks.click import ERR_INTERNAL as CLICK_ERR_INTERNAL
from app.api.webhooks.payme import ERR_INTERNAL as PAYME_ERR_INTERNAL
from app.api.webhooks.subscription import click_subscription, payme_subscription
from app.models.enums import PaymentStatus


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeDB:
    """A minimal stand-in exposing only what the except-branch touches."""

    def __init__(self):
        self.rollback = AsyncMock()
        self.commit = AsyncMock()


def _click_form(**over) -> dict:
    body = {
        "click_trans_id": "ct-1", "service_id": "svc-1",
        "click_paydoc_id": "pd-1", "merchant_trans_id": str(uuid4()),
        "amount": "100.00", "action": "0", "sign_time": "2026-01-01 00:00:00",
        "sign_string": "irrelevant-once-_authenticate-is-patched",
    }
    body.update(over)
    return body


# ── H6 — click.py /prepare, /complete: crash → structured reply, not a 500 ──

async def test_prepare_handler_crash_returns_structured_error_not_500() -> None:
    db = _FakeDB()
    form = _click_form(action="0")
    with (
        patch("app.api.webhooks.click._authenticate", AsyncMock(return_value=_Obj(tenant_id="T"))),
        patch("app.api.webhooks.click._load_payment", AsyncMock(side_effect=RuntimeError("db exploded"))),
    ):
        # OLD BUG: this exception used to propagate out of `prepare` uncaught —
        # `await prepare(...)` would raise RuntimeError instead of returning.
        result = await prepare(
            click_trans_id=form["click_trans_id"], service_id=form["service_id"],
            click_paydoc_id=form["click_paydoc_id"], merchant_trans_id=form["merchant_trans_id"],
            amount=form["amount"], action=form["action"], sign_time=form["sign_time"],
            sign_string=form["sign_string"], db=db,
        )
    assert result["error"] == ERR_INTERNAL
    assert result["click_trans_id"] == form["click_trans_id"]
    db.rollback.assert_awaited_once()


async def test_complete_handler_crash_returns_structured_error_not_500() -> None:
    db = _FakeDB()
    form = _click_form(action="1", merchant_prepare_id="mp-1", error="0", error_note="")
    with (
        patch("app.api.webhooks.click._authenticate", AsyncMock(return_value=_Obj(tenant_id="T"))),
        patch("app.api.webhooks.click._load_payment", AsyncMock(side_effect=RuntimeError("db exploded"))),
    ):
        result = await complete(
            click_trans_id=form["click_trans_id"], service_id=form["service_id"],
            merchant_trans_id=form["merchant_trans_id"], merchant_prepare_id=form["merchant_prepare_id"],
            amount=form["amount"], action=form["action"], error=form["error"],
            error_note=form["error_note"], sign_time=form["sign_time"],
            sign_string=form["sign_string"], db=db,
        )
    assert result["error"] == ERR_INTERNAL
    db.rollback.assert_awaited_once()


# ── M-05 — click.py /complete: already-approved must answer -4, not OK ──────

async def test_complete_already_approved_returns_already_paid_not_ok() -> None:
    tenant_id = "T-1"
    payment = _Obj(
        tenant_id=tenant_id, id=uuid4(), status=PaymentStatus.approved,
        amount=Decimal("100.00"), installment_id=uuid4(), external_ref="ct-1",
    )
    db = _FakeDB()
    form = _click_form(action="1", merchant_prepare_id="mp-1", error="0", error_note="")
    with (
        patch("app.api.webhooks.click._authenticate", AsyncMock(return_value=_Obj(tenant_id=tenant_id))),
        patch("app.api.webhooks.click._load_payment", AsyncMock(return_value=payment)),
    ):
        result = await complete(
            click_trans_id=form["click_trans_id"], service_id=form["service_id"],
            merchant_trans_id=form["merchant_trans_id"], merchant_prepare_id=form["merchant_prepare_id"],
            amount=form["amount"], action=form["action"], error=form["error"],
            error_note=form["error_note"], sign_time=form["sign_time"],
            sign_string=form["sign_string"], db=db,
        )
    # OLD BUG: this was `{"error": ERR_OK, "error_note": "OK", ...}` — a redelivery
    # of an already-settled payment was indistinguishable from a fresh approval.
    assert result["error"] == ERR_ALREADY_PAID
    assert result["error"] != ERR_OK
    db.commit.assert_not_awaited()  # nothing to persist — no new approval happened


# ── H6 — subscription.py /payme, /click: crash → structured reply, not a 500 ─

class _FakeSubDB:
    def __init__(self):
        self.rollback = AsyncMock()


async def test_payme_subscription_handler_crash_returns_structured_error() -> None:
    secret = "S3CR3T"
    auth = "Basic " + base64.b64encode(f"Paycom:{secret}".encode()).decode()

    class _FakeRequest:
        async def json(self):
            return {"id": 7, "method": "CheckPerformTransaction", "params": {}}

    db = _FakeSubDB()
    with (
        patch("app.api.webhooks.subscription._provider_secret", AsyncMock(return_value=secret)),
        patch("app.api.webhooks.subscription._sub_check_perform", AsyncMock(side_effect=RuntimeError("boom"))),
    ):
        result = await payme_subscription(_FakeRequest(), db, auth)
    assert result["error"]["code"] == PAYME_ERR_INTERNAL
    db.rollback.assert_awaited_once()


async def test_click_subscription_handler_crash_returns_structured_error() -> None:
    from app.api.webhooks.click import _click_sign

    secret = "click-sub-secret"
    form = {
        "click_trans_id": "ct-9", "service_id": "svc-9",
        "merchant_trans_id": "topup-9", "merchant_prepare_id": "mp-9",
        "amount": "500.00", "action": "1", "sign_time": "2026-01-01 00:00:00",
    }
    form["sign_string"] = _click_sign(
        click_trans_id=form["click_trans_id"], service_id=form["service_id"],
        secret_key=secret, merchant_trans_id=form["merchant_trans_id"],
        merchant_prepare_id=form["merchant_prepare_id"], amount=form["amount"],
        action=form["action"], sign_time=form["sign_time"],
    )

    class _FakeRequest:
        async def form(self):
            return form

    db = _FakeSubDB()
    with (
        patch("app.api.webhooks.subscription._provider_secret", AsyncMock(return_value=secret)),
        patch(
            "app.api.webhooks.subscription._credit_by_topup_id_status",
            AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        result = await click_subscription(_FakeRequest(), db)
    assert result["error"] == CLICK_ERR_INTERNAL
    db.rollback.assert_awaited_once()
