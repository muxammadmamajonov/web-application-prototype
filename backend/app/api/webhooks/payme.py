"""Payme webhook (JSON-RPC 2.0).

Payme calls us with a single endpoint that multiplexes on `method`:
  - CheckPerformTransaction — "can the customer pay X for installment Y?"
  - CreateTransaction       — "register this pending tx"
  - PerformTransaction      — "user paid; finalise"
  - CheckTransaction        — "what's the state of this tx?" (read-only)
  - CancelTransaction       — "refund / cancel"
  - GetStatement            — "give me txns in range" (reconciliation)

Auth
----
HTTP Basic with username `Paycom` and password = the tenant's
`payme_secret_key`. The request is UNAUTHENTICATED until that password is
verified, so nothing above the default (table-less) authority is held until it
is: ``_authenticate`` resolves the one candidate merchant through a narrow
capability keyed on a blind index of the presented secret, does a single
decrypt + constant-time compare, and only then pins ``app.tenant_id`` to the
signing tenant. A failed compare returns with no scope and no elevated
authority, so it can read nothing.

Idempotency
-----------
Payme retries aggressively after timeouts. We store Payme's `transaction`
(opaque string) in `Payment.external_ref` and use it for dedup:
  * `CreateTransaction` on same id → return the existing record
  * `PerformTransaction` on already-performed → return same `perform_time`
  * `CancelTransaction` on already-cancelled → return same `cancel_time`

`account` field carries `{"installment_id": "..."}` per the Cabinet config.
"""
from __future__ import annotations

import base64
import binascii
import hmac
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret
from app.core.metrics import record_payment, record_webhook
from app.core.ratelimit import webhook_limit
from app.core.webhook_identity import ProviderCredential, resolve_payme_provider
from app.db import get_db, set_tenant_scope
from app.models import Installment, Payment
from app.models.enums import (
    InstallmentStatus,
    PaymentMethod,
    PaymentStatus,
)

#: NO router-level authority. (Batch 5 independent-review correction.)
#:
#: This router used to carry ``Depends(worker_authority)``, so an
#: unauthenticated JSON-RPC request held cross-tenant reach before its Basic
#: credential had been checked. It now starts on the default ``muddato_app``
#: authority with no tenant scope — which reads nothing — resolves the merchant
#: through a narrow capability keyed on a blind index of the presented secret,
#: verifies it, and only then narrows to that one tenant. Every method handler
#: runs after ``set_tenant_scope``, so the database itself refuses another
#: tenant's payment even if a handler forgets to compare ids.
router = APIRouter(prefix="/payme", tags=["webhooks"])
log = logging.getLogger("muddato.webhooks.payme")

# Payme error codes — https://developer.help.paycom.uz/protokol-merchant-api/oshibki
ERR_AUTH                  = -32504
ERR_TRANSPORT             = -32300
ERR_METHOD_NOT_FOUND      = -32601
ERR_INTERNAL              = -32400
ERR_CANT_PERFORM          = -31008
ERR_ACCOUNT_INVALID       = -31050
ERR_AMOUNT_INVALID        = -31001
ERR_TRANSACTION_NOT_FOUND = -31003
ERR_CANT_CANCEL           = -31007

STATE_CREATED   = 1
STATE_COMPLETED = 2
STATE_CANCELLED = -1
STATE_CANCELLED_AFTER_COMPLETE = -2


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _rpc_error(rpc_id: Any, code: int, message: str, data: dict | None = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rpc_id, "error": err}


def _rpc_result(rpc_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _parse_basic(authz: str | None) -> str | None:
    """Pull the password out of `Basic base64(Paycom:secret)` — returns None
    if absent or malformed."""
    if not authz or not authz.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(authz.split(" ", 1)[1]).decode()
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    if ":" not in decoded:
        return None
    user, _, password = decoded.partition(":")
    if user != "Paycom":
        return None
    return password


async def _authenticate(db: AsyncSession, secret: str) -> ProviderCredential | None:
    """Verify the Basic credential and take the resolved tenant's scope.

    O(1): an indexed lookup by a blind HMAC of the secret, then exactly one
    decrypt + constant-time compare to verify. That shape predates this change
    (#84) and replaced a full-table scan that Fernet-decrypted every tenant's
    key on every call — an amplification-DoS vector on an unauthenticated
    endpoint.

    What IS new: the lookup runs through ``rls_webhook_payme_provider`` under
    the table-privilege-free ``muddato_webhook`` authority instead of a
    cross-tenant ORM query under ``muddato_worker``, and the scope is pinned
    here — after the compare succeeds, before any handler runs. A caller that
    fails the compare leaves with no scope and no authority above the default,
    so it can read nothing at all.
    """
    provider = await resolve_payme_provider(db, secret)
    if provider is None:
        return None
    if not hmac.compare_digest(decrypt_secret(provider.secret_ciphertext) or "", secret):
        return None
    await set_tenant_scope(db, provider.tenant_id)
    return provider


def _installment_id_from_account(account: dict | None) -> UUID | None:
    if not isinstance(account, dict):
        return None
    raw = account.get("installment_id") or account.get("installment")
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except (TypeError, ValueError):
        return None


def _amount_acceptable(inst: Installment, amount_tiyin: int) -> bool:
    """True if a Payme tiyin amount is valid for this installment: strictly  # noqa: RUF002
    positive and not exceeding the outstanding balance (principal − payoff
    discount − already paid). Payme amounts are in tiyin (1 UZS = 100 tiyin).
    Mirrors the Click amount check (#117) and the Payme contract's -31001."""
    if amount_tiyin <= 0:
        return False
    discount = inst.payoff_discount_amount or Decimal("0")
    # remaining_balance is the gross (principal - paid); the payoff discount lowers what
    # a provider may collect to settle, so subtract it here. (#876)
    outstanding = inst.remaining_balance - discount
    if outstanding <= 0:
        return False
    outstanding_tiyin = int((outstanding * 100).to_integral_value())
    return amount_tiyin <= outstanding_tiyin


@router.post("", dependencies=[webhook_limit()])
async def payme_rpc(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    # (audit/sec) Always reply with a JSON-RPC envelope at HTTP 200. A malformed
    # body or an unhandled handler exception must NOT become an HTTP 500 — Payme
    # treats a non-2xx / non-JSON-RPC reply as a failure and retries aggressively,
    # so a transient error would turn into a retry storm.
    try:
        body = await request.json()
    except Exception:
        return _rpc_error(None, ERR_INTERNAL, "invalid_json")
    if not isinstance(body, dict):
        return _rpc_error(None, ERR_INTERNAL, "invalid_request")
    rpc_id = body.get("id")
    method = str(body.get("method") or "")
    params = body.get("params") or {}

    secret = _parse_basic(authorization)
    tenant_settings = await _authenticate(db, secret) if secret else None
    if tenant_settings is None:
        record_webhook("payme", "auth_failed")  # spike = credential-stuffing signal
        return _rpc_error(rpc_id, ERR_AUTH, "auth_failed")

    # Dispatch by JSON-RPC method, then record the business outcome (ok vs error)
    # — invisible to HTTP metrics since every reply is HTTP 200.
    handlers = {
        "CheckPerformTransaction": _check_perform,
        "CreateTransaction": _create_transaction,
        "PerformTransaction": _perform_transaction,
        "CancelTransaction": _cancel_transaction,
        "CheckTransaction": _check_transaction,
        "GetStatement": _get_statement,
    }
    handler = handlers.get(method)
    if handler is None:
        record_webhook("payme", "error")
        return _rpc_error(rpc_id, ERR_METHOD_NOT_FOUND, "method_not_found")
    try:
        resp = await handler(db, tenant_settings, rpc_id, params)
    except Exception:
        log.exception("payme handler crashed: method=%s", method)
        await db.rollback()
        record_webhook("payme", "error")
        return _rpc_error(rpc_id, ERR_INTERNAL, "internal_error")
    record_webhook("payme", "error" if "error" in resp else "ok")
    return resp


# ── method handlers ───────────────────────────────────────────────────────

async def _check_perform(
    db: AsyncSession, ts: ProviderCredential, rpc_id: Any, params: dict,
) -> dict:
    inst_id = _installment_id_from_account(params.get("account"))
    if inst_id is None:
        return _rpc_error(rpc_id, ERR_ACCOUNT_INVALID, "installment_id_required")
    inst = (await db.execute(
        select(Installment).where(
            Installment.id == inst_id,
            Installment.tenant_id == ts.tenant_id,
        )
    )).scalar_one_or_none()
    if inst is None:
        return _rpc_error(rpc_id, ERR_ACCOUNT_INVALID, "installment_not_found")
    # Payme passes amount in tiyin (1 UZS = 100 tiyin).
    try:
        amount_tiyin = int(params.get("amount", 0))
    except (TypeError, ValueError):
        return _rpc_error(rpc_id, ERR_AMOUNT_INVALID, "amount_invalid")
    if not _amount_acceptable(inst, amount_tiyin):
        return _rpc_error(rpc_id, ERR_AMOUNT_INVALID, "amount_invalid")
    if inst.status in (InstallmentStatus.completed, InstallmentStatus.cancelled):
        return _rpc_error(rpc_id, ERR_CANT_PERFORM, "installment_closed")
    return _rpc_result(rpc_id, {"allow": True})


async def _create_transaction(
    db: AsyncSession, ts: ProviderCredential, rpc_id: Any, params: dict,
) -> dict:
    inst_id = _installment_id_from_account(params.get("account"))
    if inst_id is None:
        return _rpc_error(rpc_id, ERR_ACCOUNT_INVALID, "installment_id_required")
    tx_id = str(params.get("id") or "")
    if not tx_id:
        return _rpc_error(rpc_id, ERR_INTERNAL, "transaction_id_required")
    time_ms = int(params.get("time") or _now_ms())
    try:
        amount_tiyin = int(params.get("amount", 0))
    except (TypeError, ValueError):
        return _rpc_error(rpc_id, ERR_AMOUNT_INVALID, "amount_invalid")

    inst = (await db.execute(
        select(Installment).where(
            Installment.id == inst_id,
            Installment.tenant_id == ts.tenant_id,
        )
    )).scalar_one_or_none()
    if inst is None:
        return _rpc_error(rpc_id, ERR_ACCOUNT_INVALID, "installment_not_found")

    # Idempotency: same transaction id → return existing row. Scope to the
    # authenticated tenant so a foreign external_ref can never leak another
    # tenant's payment id/state (audit).
    existing = (await db.execute(
        select(Payment).where(Payment.external_ref == tx_id, Payment.tenant_id == ts.tenant_id)
    )).scalar_one_or_none()
    if existing is not None:
        state = (
            STATE_COMPLETED if existing.status == PaymentStatus.approved
            else STATE_CANCELLED if existing.status == PaymentStatus.refunded
            else STATE_CREATED
        )
        return _rpc_result(rpc_id, {
            "transaction": str(existing.id),
            "state": state,
            "create_time": int((existing.created_at.timestamp() if existing.created_at else 0) * 1000),
        })

    # (#117) Reject an amount that doesn't fit the installment (positive, not
    # exceeding the outstanding balance) before creating a Payment row.
    if not _amount_acceptable(inst, amount_tiyin):
        return _rpc_error(rpc_id, ERR_AMOUNT_INVALID, "amount_invalid")
    # (audit/sec) Reject a NEW transaction against a closed loan. Payme does not
    # require CheckPerformTransaction first, so without this guard a CANCELLED or
    # COMPLETED installment could be reactivated and credited via CreateTransaction
    # (the existing-tx idempotency branch above still returns prior state).
    if inst.status in (InstallmentStatus.completed, InstallmentStatus.cancelled):
        return _rpc_error(rpc_id, ERR_CANT_PERFORM, "installment_closed")

    amount = Decimal(amount_tiyin) / Decimal(100)
    pay = Payment(
        tenant_id=ts.tenant_id,
        installment_id=inst.id,
        customer_id=inst.customer_id,
        currency=inst.currency,          # from the contract, never the shop
        amount=amount,
        method=PaymentMethod.payme,
        status=PaymentStatus.pending,
        external_ref=tx_id,
        provider_payload={"params": params},
    )
    db.add(pay)
    try:
        await db.commit()
    except IntegrityError:
        # Concurrent CreateTransaction for the same tx_id raced us to the
        # unique(external_ref) index. Roll back and return the row that won.
        await db.rollback()
        existing = (await db.execute(
            select(Payment).where(Payment.external_ref == tx_id, Payment.tenant_id == ts.tenant_id)
        )).scalar_one_or_none()
        if existing is not None:
            return _rpc_result(rpc_id, {
                "transaction": str(existing.id),
                "state": STATE_CREATED,
                "create_time": int((existing.created_at.timestamp() if existing.created_at else 0) * 1000),
            })
        raise
    await db.refresh(pay)

    return _rpc_result(rpc_id, {
        "transaction": str(pay.id),
        "state": STATE_CREATED,
        "create_time": time_ms,
    })


async def _perform_transaction(db: AsyncSession, ts: ProviderCredential, rpc_id: Any, params: dict) -> dict:
    tx_id = str(params.get("id") or "")
    # Lock the payment row so concurrent Payme retries serialize and can't both
    # pass the "not yet approved" guard and double-credit the installment.
    pay = (await db.execute(
        select(Payment).where(Payment.external_ref == tx_id).with_for_update()
    )).scalar_one_or_none()
    if pay is None or pay.tenant_id != ts.tenant_id:
        return _rpc_error(rpc_id, ERR_TRANSACTION_NOT_FOUND, "transaction_not_found")

    if pay.status == PaymentStatus.approved:
        # Idempotent: return existing perform_time.
        perform_ms = int((pay.paid_at.timestamp() if pay.paid_at else 0) * 1000)
        return _rpc_result(rpc_id, {
            "transaction": str(pay.id),
            "state": STATE_COMPLETED,
            "perform_time": perform_ms,
        })
    if pay.status == PaymentStatus.refunded:
        return _rpc_error(rpc_id, ERR_CANT_PERFORM, "transaction_cancelled")

    # The contract can be cancelled after CreateTransaction but before Perform.
    # Check it under the same row lock used for the eventual balance mutation;
    # leave the provider transaction pending so no money/status is fabricated.
    inst = (await db.execute(
        select(Installment).where(Installment.id == pay.installment_id).with_for_update()
    )).scalar_one_or_none()
    from app.services.installments import accepts_payment, overpayment_excess
    if inst is None or not accepts_payment(inst):
        return _rpc_error(rpc_id, ERR_CANT_PERFORM, "installment_closed")
    if overpayment_excess(inst, pay.amount or Decimal("0")):
        return _rpc_error(rpc_id, ERR_AMOUNT_INVALID, "amount_invalid")

    now = datetime.now(UTC)
    prior_status = pay.status
    pay.status = PaymentStatus.approved
    pay.paid_at = now
    pay.approved_at = now
    pay.method = PaymentMethod.payme

    # Apply to installment (locked to serialize balance updates). Schedule is
    # derived from paid_amount so partial/over payments advance it correctly.
    if inst is not None:
        from app.services.installments import apply_payment
        apply_payment(inst, pay.amount or Decimal("0"), now=now)
    record_payment("payme", "approved")  # counted once — the idempotent retry returns above
    await db.commit()
    # Opaque ids and the status edge only — never the RPC params, the Basic-auth
    # secret or the amount. The idempotent replay above returns before this point,
    # so one commit, one line.
    log.info(
        "payment.transition provider=payme payment=%s tenant=%s from=%s to=approved",
        pay.id, pay.tenant_id, prior_status,
    )

    return _rpc_result(rpc_id, {
        "transaction": str(pay.id),
        "state": STATE_COMPLETED,
        "perform_time": int(now.timestamp() * 1000),
    })


async def _cancel_transaction(db: AsyncSession, ts: ProviderCredential, rpc_id: Any, params: dict) -> dict:
    tx_id = str(params.get("id") or "")
    pay = (await db.execute(
        select(Payment).where(Payment.external_ref == tx_id).with_for_update()
    )).scalar_one_or_none()
    if pay is None or pay.tenant_id != ts.tenant_id:
        return _rpc_error(rpc_id, ERR_TRANSACTION_NOT_FOUND, "transaction_not_found")

    if pay.status == PaymentStatus.refunded:
        # Idempotent
        cancel_ms = int((pay.approved_at.timestamp() if pay.approved_at else 0) * 1000)
        return _rpc_result(rpc_id, {
            "transaction": str(pay.id),
            "state": (
                STATE_CANCELLED_AFTER_COMPLETE if (pay.provider_payload or {}).get("was_completed")
                else STATE_CANCELLED
            ),
            "cancel_time": cancel_ms,
        })

    was_completed = pay.status == PaymentStatus.approved
    prior_status = pay.status
    now = datetime.now(UTC)
    pay.status = PaymentStatus.refunded
    pay.provider_payload = {**(pay.provider_payload or {}), "was_completed": was_completed, "cancel_reason": params.get("reason")}

    # Reverse the balance if we already counted it; schedule re-derives.
    if was_completed:
        inst = (await db.execute(
            select(Installment).where(Installment.id == pay.installment_id).with_for_update()
        )).scalar_one_or_none()
        if inst is not None:
            from app.services.installments import reverse_payment
            reverse_payment(inst, pay.amount or Decimal("0"), now=now)
    record_payment("payme", "refunded")  # counted once — the idempotent retry returns above
    await db.commit()
    # from=approved is the cancel-after-perform case, where the balance above was
    # reversed; any other prior status means nothing had been credited.
    log.info(
        "payment.transition provider=payme payment=%s tenant=%s from=%s to=refunded",
        pay.id, pay.tenant_id, prior_status,
    )

    return _rpc_result(rpc_id, {
        "transaction": str(pay.id),
        "state": STATE_CANCELLED_AFTER_COMPLETE if was_completed else STATE_CANCELLED,
        "cancel_time": int(now.timestamp() * 1000),
    })


async def _check_transaction(db: AsyncSession, ts: ProviderCredential, rpc_id: Any, params: dict) -> dict:
    tx_id = str(params.get("id") or "")
    pay = (await db.execute(
        select(Payment).where(Payment.external_ref == tx_id)
    )).scalar_one_or_none()
    if pay is None or pay.tenant_id != ts.tenant_id:
        return _rpc_error(rpc_id, ERR_TRANSACTION_NOT_FOUND, "transaction_not_found")

    state = (
        STATE_COMPLETED if pay.status == PaymentStatus.approved
        else STATE_CANCELLED if pay.status == PaymentStatus.refunded
        else STATE_CREATED
    )
    return _rpc_result(rpc_id, {
        "transaction": str(pay.id),
        "state": state,
        "create_time": int((pay.created_at.timestamp() if pay.created_at else 0) * 1000),
        "perform_time": int((pay.paid_at.timestamp() if pay.paid_at else 0) * 1000),
        "cancel_time": 0,
        "reason": (pay.provider_payload or {}).get("cancel_reason"),
    })


async def _get_statement(
    db: AsyncSession, ts: ProviderCredential, rpc_id: Any, params: dict,
) -> dict:
    """Return Payme-shaped statement rows for the [from, to] millisecond range."""
    try:
        from_ms = int(params.get("from", 0))
        to_ms = int(params.get("to", 0))
    except (TypeError, ValueError):
        return _rpc_error(rpc_id, ERR_INTERNAL, "bad_range")

    if to_ms <= 0 or to_ms < from_ms:
        return _rpc_result(rpc_id, {"transactions": []})

    from datetime import datetime as _dt
    from_dt = _dt.fromtimestamp(from_ms / 1000, tz=UTC)
    to_dt = _dt.fromtimestamp(to_ms / 1000, tz=UTC)

    rows = (await db.execute(
        select(Payment).where(
            Payment.tenant_id == ts.tenant_id,
            Payment.method == PaymentMethod.payme,
            Payment.created_at >= from_dt,
            Payment.created_at <= to_dt,
        ).order_by(Payment.created_at.asc()).limit(500)
    )).scalars().all()

    txns = []
    for p in rows:
        state = (
            STATE_COMPLETED if p.status == PaymentStatus.approved
            else STATE_CANCELLED if p.status == PaymentStatus.refunded
            else STATE_CREATED
        )
        txns.append({
            "id": p.external_ref,
            "time": int((p.created_at.timestamp() if p.created_at else 0) * 1000),
            "amount": int((p.amount or Decimal("0")) * 100),
            "account": {"installment_id": str(p.installment_id)},
            "create_time": int((p.created_at.timestamp() if p.created_at else 0) * 1000),
            "perform_time": int((p.paid_at.timestamp() if p.paid_at else 0) * 1000),
            "cancel_time": 0,
            "transaction": str(p.id),
            "state": state,
            "reason": (p.provider_payload or {}).get("cancel_reason"),
        })
    return _rpc_result(rpc_id, {"transactions": txns})


__all__ = ("router",)
