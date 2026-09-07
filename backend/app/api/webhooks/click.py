"""Click webhook — real implementation.

Click flow (Stage 0 click.uz initiated)
--------------------------------------
1. Mini-App posts /me/payments/initiate → backend creates a `pending`
   Payment row and returns a Click deep-link
   `https://my.click.uz/services/pay?service_id=...&merchant_id=...
    &amount=...&transaction_param=<payment_id>&return_url=...`
2. User pays in Click app.
3. Click POSTs **prepare** here → we verify sign, return merchant_prepare_id.
4. Click POSTs **complete** here → we approve the Payment, bump
   `installment.paid_amount/paid_count`, schedule `next_due_date`.

Auth
----
Click signs each webhook with
`md5(click_trans_id + service_id + secret_key + merchant_trans_id
  [+ merchant_prepare_id] + amount + action + sign_time)`.
We look up the secret_key by `service_id` (each tenant has their own
in `tenant_settings.click_service_id` + `click_secret_key`).

Idempotency
-----------
* prepare: returns the same merchant_prepare_id for the same
  click_trans_id (no row created on re-fire).
* complete: refuses to double-approve a Payment already at
  `status=approved` for this `external_ref`.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret
from app.core.metrics import record_payment, record_webhook
from app.core.ratelimit import webhook_limit
from app.core.webhook_identity import ProviderCredential, resolve_click_provider
from app.db import get_db, set_tenant_scope
from app.models import Installment, Payment
from app.models.enums import PaymentMethod, PaymentStatus

#: NO router-level authority. (Batch 5 independent-review correction.)
#:
#: This router used to carry ``Depends(worker_authority)``, which handed every
#: unauthenticated request cross-tenant database reach before it had proved
#: anything — and the handlers then performed a global ``tenant_settings``
#: lookup with it, *before* checking the Click signature.
#:
#: An unauthenticated callback now starts on the default ``muddato_app``
#: authority with no tenant scope, which reads no tenant row at all. The order
#: is: parse → resolve exactly one provider through a narrow capability →
#: verify the signature → reject uniformly → narrow to the resolved tenant →
#: only then touch the payment. ``_authenticate`` below is the whole of it, and
#: both endpoints go through it.
router = APIRouter(prefix="/click", tags=["webhooks"])
log = logging.getLogger("muddato.webhooks.click")

# Click error codes — https://docs.click.uz/click-api/
ERR_OK = 0
ERR_SIGN_CHECK_FAILED = -1
ERR_BAD_AMOUNT = -2
ERR_ACTION_NOT_FOUND = -3
ERR_ALREADY_PAID = -4
ERR_TRANSACTION_NOT_FOUND = -5
ERR_TRANSACTION_CANCELLED = -9
ERR_INTERNAL = -8  # (H6) generic handler-crash reply — see `prepare`/`complete`

ACTION_PREPARE = "0"
ACTION_COMPLETE = "1"


def _click_sign(
    *,
    click_trans_id: str,
    service_id: str,
    secret_key: str,
    merchant_trans_id: str,
    amount: str,
    action: str,
    sign_time: str,
    merchant_prepare_id: str | None = None,
) -> str:
    """Compute the Click signature.

    Stage prepare:  md5(trans + svc + key + mtid + amount + action + ts)
    Stage complete: md5(trans + svc + key + mtid + mpid + amount + action + ts)
    """
    parts = [click_trans_id, service_id, secret_key, merchant_trans_id]
    if merchant_prepare_id is not None:
        parts.append(merchant_prepare_id)
    parts += [amount, action, sign_time]
    return hashlib.md5("".join(parts).encode()).hexdigest()


async def _authenticate(
    db: AsyncSession,
    *,
    service_id: str,
    sign_string: str,
    **signed: str,
) -> ProviderCredential | None:
    """Resolve the merchant, verify the signature, and only then take a scope.

    Returns the credential on success and None on ANY failure — unknown
    ``service_id``, no configured key, an undecryptable key, a bad signature.
    One return value for all of them on purpose: the caller answers with a
    single ``sign_failed``, so the response cannot be used to learn whether a
    merchant id exists (see ``_err`` call sites).

    On success it pins ``app.tenant_id`` to the signing tenant BEFORE the
    handler reads anything. From that point the database itself will not hand
    over another tenant's payment, so the in-handler ownership check that
    follows is a second answer to the same question rather than the only one.
    """
    provider = await resolve_click_provider(db, service_id)
    if provider is None:
        return None
    secret = decrypt_secret(provider.secret_ciphertext)
    if not secret:
        return None

    # `**signed` is `str`, not `str | None`: `/prepare` OMITS
    # `merchant_prepare_id` rather than passing None for it, which is what
    # `_click_sign` distinguishes on. Typing it accurately is what removed the
    # `type: ignore` that used to sit here.
    expected = _click_sign(service_id=service_id, secret_key=secret, **signed)
    if not hmac.compare_digest(expected.encode(), (sign_string or "").lower().encode()):
        record_webhook("click", "auth_failed")  # bad signature — forgery/credential signal
        return None

    # Authenticated. NOW the request may reach data, and only this tenant's.
    await set_tenant_scope(db, provider.tenant_id)
    return provider


async def _load_payment(db: AsyncSession, payment_id: str, *, for_update: bool = False) -> Payment | None:
    try:
        stmt = select(Payment).where(Payment.id == UUID(payment_id))
        if for_update:
            # Serialize concurrent webhook deliveries for the same payment so
            # two near-simultaneous `complete` calls can't both pass the
            # "not yet approved" guard and double-credit the installment.
            stmt = stmt.with_for_update()
        return (await db.execute(stmt)).scalar_one_or_none()
    except ValueError:
        return None


def _err(click_trans_id: str, mtid: str, mprep: str | None, code: int, note: str) -> dict:
    out = {
        "click_trans_id": click_trans_id,
        "merchant_trans_id": mtid,
        "error": code,
        "error_note": note,
    }
    if mprep is not None:
        out["merchant_prepare_id"] = mprep
    return out


@router.post("/prepare", dependencies=[webhook_limit()])
async def prepare(
    click_trans_id: Annotated[str, Form()],
    service_id: Annotated[str, Form()],
    click_paydoc_id: Annotated[str, Form()],
    merchant_trans_id: Annotated[str, Form()],
    amount: Annotated[str, Form()],
    action: Annotated[str, Form()],
    sign_time: Annotated[str, Form()],
    sign_string: Annotated[str, Form()],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Stage 1 — Click asks if we accept this payment."""
    if action != ACTION_PREPARE:
        return _err(click_trans_id, merchant_trans_id, None, ERR_ACTION_NOT_FOUND, "bad_action")

    # (H6) Click expects a structured JSON-RPC-style error body at HTTP 200 for
    # every outcome, including a crash — an uncaught exception here would surface
    # as a raw HTTP 500, which Click either retries blindly or parks the
    # transaction in an "unknown" state. Mirrors the payme.py handler-crash guard.
    try:
        settings_row = await _authenticate(
            db,
            service_id=service_id,
            sign_string=sign_string,
            click_trans_id=click_trans_id,
            merchant_trans_id=merchant_trans_id,
            amount=amount,
            action=action,
            sign_time=sign_time,
        )
        if settings_row is None:
            log.warning("click.prepare.auth_failed trans=%s", click_trans_id)
            return _err(click_trans_id, merchant_trans_id, None, ERR_SIGN_CHECK_FAILED, "sign_failed")

        payment = await _load_payment(db, merchant_trans_id, for_update=True)  # (#118) lock like /complete
        if payment is None:
            return _err(click_trans_id, merchant_trans_id, None, ERR_TRANSACTION_NOT_FOUND, "payment_not_found")
        # Tenant isolation: the signature only proves *a* tenant's secret signed
        # this request; the referenced payment must belong to that same tenant.
        # Without this a tenant could approve/cancel another tenant's payment by
        # signing a webhook that names the victim's payment id with their own key.
        if payment.tenant_id != settings_row.tenant_id:
            log.warning("click.prepare.cross_tenant svc=%s pay=%s", service_id, payment.id)
            return _err(click_trans_id, merchant_trans_id, None, ERR_TRANSACTION_NOT_FOUND, "payment_not_found")

        # Verify amount (Click sends "12345.00")
        try:
            click_amount = Decimal(str(amount))
        except (ValueError, ArithmeticError):
            return _err(click_trans_id, merchant_trans_id, None, ERR_BAD_AMOUNT, "amount_invalid")
        if click_amount != (payment.amount or Decimal("0")):
            return _err(click_trans_id, merchant_trans_id, None, ERR_BAD_AMOUNT, "amount_mismatch")

        # Cancelled already? Don't let Click "un-cancel".
        if payment.status == PaymentStatus.refunded:
            return _err(click_trans_id, merchant_trans_id, None, ERR_TRANSACTION_CANCELLED, "cancelled")
        if payment.status == PaymentStatus.approved:
            # Already approved on a prior round — Click is just retrying; reply OK with stored prepare id.
            return {
                "click_trans_id": click_trans_id,
                "merchant_trans_id": merchant_trans_id,
                "merchant_prepare_id": payment.external_ref or str(payment.id),
                "error": ERR_OK,
                "error_note": "OK",
            }

        # Mark as prepared (we just store click_trans_id; idempotent).
        payment.external_ref = click_trans_id
        payment.method = PaymentMethod.click
        await db.commit()

        return {
            "click_trans_id": click_trans_id,
            "merchant_trans_id": merchant_trans_id,
            "merchant_prepare_id": str(payment.id),
            "error": ERR_OK,
            "error_note": "OK",
        }
    except Exception:
        log.exception("click.prepare.handler_crashed trans=%s", click_trans_id)
        await db.rollback()
        record_webhook("click", "error")
        return _err(click_trans_id, merchant_trans_id, None, ERR_INTERNAL, "internal_error")


@router.post("/complete", dependencies=[webhook_limit()])
async def complete(
    click_trans_id: Annotated[str, Form()],
    service_id: Annotated[str, Form()],
    merchant_trans_id: Annotated[str, Form()],
    merchant_prepare_id: Annotated[str, Form()],
    amount: Annotated[str, Form()],
    action: Annotated[str, Form()],
    error: Annotated[str, Form()],
    error_note: Annotated[str, Form()],
    sign_time: Annotated[str, Form()],
    sign_string: Annotated[str, Form()],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Stage 2 — user paid in Click app; we approve the Payment."""
    if action != ACTION_COMPLETE:
        return _err(click_trans_id, merchant_trans_id, merchant_prepare_id, ERR_ACTION_NOT_FOUND, "bad_action")

    # (H6) Same crash guard as /prepare — a handler exception must become a
    # structured Click error reply, not a raw HTTP 500.
    try:
        settings_row = await _authenticate(
            db,
            service_id=service_id,
            sign_string=sign_string,
            click_trans_id=click_trans_id,
            merchant_trans_id=merchant_trans_id,
            merchant_prepare_id=merchant_prepare_id,
            amount=amount,
            action=action,
            sign_time=sign_time,
        )
        if settings_row is None:
            return _err(
                click_trans_id, merchant_trans_id, merchant_prepare_id,
                ERR_SIGN_CHECK_FAILED, "sign_failed",
            )

        payment = await _load_payment(db, merchant_trans_id, for_update=True)
        if payment is None:
            return _err(click_trans_id, merchant_trans_id, merchant_prepare_id, ERR_TRANSACTION_NOT_FOUND, "payment_not_found")
        # Tenant isolation (see /prepare) — payment must belong to the signing tenant.
        if payment.tenant_id != settings_row.tenant_id:
            log.warning("click.complete.cross_tenant svc=%s pay=%s", service_id, payment.id)
            return _err(click_trans_id, merchant_trans_id, merchant_prepare_id, ERR_TRANSACTION_NOT_FOUND, "payment_not_found")

        # Click signals error=-9 when the user cancelled mid-flow.
        if str(error) != "0":
            prior_status = payment.status
            if prior_status == PaymentStatus.approved:
                inst = (await db.execute(
                    select(Installment)
                    .where(Installment.id == payment.installment_id)
                    .with_for_update()
                )).scalar_one_or_none()
                if inst is not None:
                    from app.services.installments import reverse_payment
                    reverse_payment(inst, payment.amount or Decimal("0"), now=datetime.now(UTC))
            payment.status = PaymentStatus.refunded
            await db.commit()
            record_payment("click", "refunded")  # (#143) user-cancelled mid-flow
            # Opaque ids and the status edge only — never the signature, the merchant
            # secret or the form body.
            log.info(
                "payment.transition provider=click payment=%s tenant=%s from=%s to=refunded",
                payment.id, payment.tenant_id, prior_status,
            )
            return {
                "click_trans_id": click_trans_id,
                "merchant_trans_id": merchant_trans_id,
                "merchant_confirm_id": str(payment.id),
                "error": ERR_TRANSACTION_CANCELLED,
                "error_note": "cancelled_by_user",
            }

        # (M-05) Click redelivers `complete` at least once, and two near-simultaneous
        # deliveries can both reach here before either commits (the row lock above
        # only serializes them, it doesn't make the loser's request "new"). Either
        # way this call did NOT just newly approve the payment — it was already
        # `approved`. Track that up front so the reply below can say -4
        # ("already paid") instead of silently re-confirming with ERR_OK, which is
        # indistinguishable from a fresh approval on Click's side.
        already_approved = payment.status == PaymentStatus.approved

        if not already_approved:
            inst = (await db.execute(
                select(Installment).where(Installment.id == payment.installment_id).with_for_update()
            )).scalar_one_or_none()
            from app.services.installments import accepts_payment, overpayment_excess
            if inst is None or not accepts_payment(inst):
                return _err(
                    click_trans_id, merchant_trans_id, merchant_prepare_id,
                    ERR_TRANSACTION_CANCELLED, "installment_closed",
                )
            if overpayment_excess(inst, payment.amount or Decimal("0")):
                return _err(
                    click_trans_id, merchant_trans_id, merchant_prepare_id,
                    ERR_BAD_AMOUNT, "amount_invalid",
                )

            now = datetime.now(UTC)
            prior_status = payment.status
            payment.status = PaymentStatus.approved
            payment.paid_at = now
            payment.approved_at = now
            payment.method = PaymentMethod.click
            # Apply to the installment (locked so concurrent payments on the same
            # installment serialize their balance updates). The schedule is derived
            # from paid_amount, so partial/over payments advance it correctly.
            if inst is not None:
                from app.services.installments import apply_payment
                apply_payment(inst, payment.amount or Decimal("0"), now=now)
            await db.commit()
            record_payment("click", "approved")  # (#143) counted once — idempotent retries skip this block
            # Inside the same guard as record_payment, so an idempotent redelivery skips
            # it too — one commit, one line.
            log.info(
                "payment.transition provider=click payment=%s tenant=%s from=%s to=approved",
                payment.id, payment.tenant_id, prior_status,
            )

        record_webhook("click", "ok")  # (#143) successful complete webhook
        if already_approved:
            return _err(
                click_trans_id, merchant_trans_id, merchant_prepare_id,
                ERR_ALREADY_PAID, "already_paid",
            )
        return {
            "click_trans_id": click_trans_id,
            "merchant_trans_id": merchant_trans_id,
            "merchant_confirm_id": str(payment.id),
            "error": ERR_OK,
            "error_note": "OK",
        }
    except Exception:
        log.exception("click.complete.handler_crashed trans=%s", click_trans_id)
        await db.rollback()
        record_webhook("click", "error")
        return _err(click_trans_id, merchant_trans_id, merchant_prepare_id, ERR_INTERNAL, "internal_error")


__all__ = ("_click_sign", "router")
