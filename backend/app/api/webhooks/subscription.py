"""Platform subscription-payment webhooks (Round 37; Payme JSON-RPC #858).

These are distinct from the per-tenant installment webhooks (``payme.py`` /
``click.py``), which settle a *customer's* payment to a *tenant*. Here a *tenant*
pays the *platform* to top up its prepaid subscription balance, so the money lands
in the PLATFORM's Payme/Click merchant account and the callback is authenticated
with the platform credentials stored in the ``billing_providers`` setting (not a
tenant's keys).

Payme (#858)
------------
Full JSON-RPC merchant protocol, mirroring the per-tenant ``payme.py`` but keyed on
a pre-existing ``TenantTopup`` instead of creating a Payment row:

* ``account.topup_id`` identifies the top-up. **[OPERATOR]** the exact Cabinet
  account-field name is confirmed when the platform Payme credentials are wired;
  this assumes ``topup_id`` (the scaffold's existing assumption).
* The Payme transaction id lives in ``TenantTopup.provider_ref`` (uniquely indexed
  → idempotency); the state machine (state / create-perform-cancel times /
  was_completed / reason) is persisted in ``TenantTopup.provider_payload``.
* ``PerformTransaction`` credits via the locked, idempotent ``mark_topup_paid``;
  ``CancelTransaction`` reverses a performed credit (debits the balance back).

Status: the provider stays disabled until a platform Payme secret is configured in
``billing_providers`` (``_provider_secret`` → 503), so this cannot move money until
the operator enables it.

Authority (Batch 5 independent-review correction)
-------------------------------------------------
The router grants none. An unauthenticated callback runs on the default
``muddato_app`` authority with no tenant scope — which reaches no tenant row —
reads the ONE platform provider object it needs through a narrow capability
under the table-privilege-free ``muddato_webhook`` authority, verifies the
credential, and only then narrows itself to the tenant that owns the top-up
being settled. Every ``tenant_topups`` read and write below therefore happens
under an exact tenant scope, so the database refuses a foreign top-up even if a
handler forgets to check.
"""
from __future__ import annotations

import hmac
import logging
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.webhooks.click import ERR_BAD_AMOUNT as CLICK_ERR_BAD_AMOUNT
from app.api.webhooks.click import ERR_INTERNAL as CLICK_ERR_INTERNAL
from app.api.webhooks.click import ERR_OK as CLICK_ERR_OK
from app.api.webhooks.click import _click_sign
from app.api.webhooks.payme import (
    ERR_ACCOUNT_INVALID,
    ERR_AMOUNT_INVALID,
    ERR_AUTH,
    ERR_CANT_CANCEL,
    ERR_CANT_PERFORM,
    ERR_INTERNAL,
    ERR_METHOD_NOT_FOUND,
    ERR_TRANSACTION_NOT_FOUND,
    STATE_CANCELLED,
    STATE_CANCELLED_AFTER_COMPLETE,
    STATE_COMPLETED,
    STATE_CREATED,
    _now_ms,
    _parse_basic,
    _rpc_error,
    _rpc_result,
)
from app.core.metrics import record_webhook
from app.core.money_utils import round_amount
from app.core.ratelimit import webhook_limit
from app.core.webhook_identity import (
    resolve_billing_provider,
    resolve_topup_tenant,
    resolve_topup_tenant_by_ref,
)
from app.db import get_db, set_tenant_scope
from app.models import Tenant, TenantBalanceTxn, TenantTopup
from app.services import audit
from app.services import subscription_checkout as sc

#: NO router-level authority. (Batch 5 independent-review correction.)
#:
#: This router used to carry ``Depends(worker_authority)``, so an
#: unauthenticated billing callback held cross-tenant reach before the platform
#: merchant secret had been checked — and the very first thing each handler did
#: was read the ``billing_providers`` platform setting with it.
#:
#: Now: the request starts on the default ``muddato_app`` authority with no
#: tenant scope; ``resolve_billing_provider`` reads ONE provider object under
#: the table-privilege-free ``muddato_webhook`` authority; the credential is
#: compared; and only afterwards is the request narrowed to the tenant that owns
#: the top-up being settled (``_scope_to_topup`` / ``_scope_to_ref``).
router = APIRouter(prefix="/subscription", tags=["webhooks"])
log = logging.getLogger("muddato.webhooks.subscription")


def _secret_of(p: dict[str, Any] | None, name: str) -> str | None:
    """The platform merchant secret inside one provider object, tolerating a few
    key spellings. None when the provider is missing/disabled or has no secret —
    callers treat that as "route not wired" and answer 503, so an unconfigured
    webhook can never credit a balance (#64)."""
    if not isinstance(p, dict) or not p.get("enabled"):
        return None
    secret = p.get("secret_key") or p.get(f"{name}_secret_key") or p.get("key")
    return str(secret) if secret else None


async def _provider_secret(db: AsyncSession, name: str) -> str | None:
    """Fetch one platform provider's secret through the narrow capability.

    ``billing_providers`` is a platform-only settings key: it is deliberately
    absent from the tenant-readable allowlist, so no ordinary authority can read
    it. ``resolve_billing_provider`` reaches exactly that one key's ``name``
    member under ``muddato_webhook``, which holds no table privilege at all, and
    hands the authority straight back.
    """
    return _secret_of(await resolve_billing_provider(db, name), name)


async def _scope_to_topup(db: AsyncSession, topup_id: object) -> bool:
    """Narrow an AUTHENTICATED callback to the tenant that owns ``topup_id``.

    Called only after the platform credential has been verified. Returns False
    when the top-up does not exist, which the caller reports with the same
    outcome it uses for any other unusable reference — an unknown id and a
    foreign id are indistinguishable from outside.
    """
    tenant_id = await resolve_topup_tenant(db, topup_id)
    if tenant_id is None:
        return False
    await set_tenant_scope(db, tenant_id)
    return True


async def _scope_to_ref(db: AsyncSession, provider_ref: str) -> bool:
    """Same, for the Payme methods that name a transaction rather than an account."""
    tenant_id = await resolve_topup_tenant_by_ref(db, provider_ref)
    if tenant_id is None:
        return False
    await set_tenant_scope(db, tenant_id)
    return True


def _amount_or_mismatch(raw: Any) -> Decimal | None:
    """Click reports the captured amount in so'm. Returns it, None when absent, or
    a forced-mismatch sentinel when present-but-unparseable."""
    if raw in (None, ""):
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("-1")


# _credit_by_topup_id / _credit_by_topup_id_status outcomes (F-10). A caller that
# speaks a *retrying* provider protocol (Click) must distinguish a benign "nothing
# more to do" from a genuine refusal: replying "success" on a refusal makes the
# provider stop retrying while the platform holds money it never credited.
#   CREDIT_OK      — balance credited this call.
#   CREDIT_ALREADY — idempotent no-op: this top-up is already paid / a duplicate
#                    provider_ref lost the race to the winning delivery. Funds are
#                    credited exactly once → treat as success.
#   CREDIT_REFUSED — fail-closed rejection: missing/short/unparseable captured
#                    amount, or an unknown/not-found top-up. Money was received but
#                    NOT credited and there is no audit row → surface an error so the
#                    provider retries and a human reconciles.
CREDIT_OK = "credited"
CREDIT_ALREADY = "already"
CREDIT_REFUSED = "refused"


async def _credit_by_topup_id_status(
    db: AsyncSession, topup_id: str, *, provider_ref: str | None,
    captured_amount: Decimal | None,
) -> str:
    """Credit a top-up's balance and classify the outcome (see CREDIT_* above).

    This is the richer-return core; ``_credit_by_topup_id`` wraps it with the
    historical bool contract (True == credited this call) that other callers/tests
    rely on. (#857) A provider finalisation must carry the captured amount so we can
    verify it matches the requested top-up before crediting — never credit a balance
    without that check. Fail closed: refuse + log. The admin card-approval path
    credits via platform.py (mark_topup_paid with no amount), not this helper.
    """
    if captured_amount is None:
        log.warning("subscription.credit.refused_no_captured_amount topup=%s", topup_id)
        return CREDIT_REFUSED
    try:
        tid = UUID(topup_id)
    except (ValueError, TypeError):
        log.warning("subscription.credit.refused_bad_topup_id topup=%s", topup_id)
        return CREDIT_REFUSED
    # Narrow to the owning tenant first. Callers reach here only after the
    # platform credential has been verified; an unresolvable id is refused with
    # the same outcome as any other unknown top-up.
    if not await _scope_to_topup(db, tid):
        log.warning("subscription.credit.refused_topup_not_found topup=%s", topup_id)
        return CREDIT_REFUSED
    topup = (await db.execute(
        select(TenantTopup).where(TenantTopup.id == tid)
    )).scalar_one_or_none()
    if topup is None:
        log.warning("subscription.credit.refused_topup_not_found topup=%s", topup_id)
        return CREDIT_REFUSED
    # An already-paid top-up is a benign redelivery, not a refusal (mark_topup_paid
    # also returns False here under its lock). Distinguish it up-front so a retried
    # finalisation reports success rather than a spurious amount/refusal error.
    if topup.status == "paid":
        return CREDIT_ALREADY
    # A mismatched captured amount is a hard refusal: the tenant must not request a
    # large top-up and pay less. mark_topup_paid re-checks this under the row lock.
    if captured_amount != topup.amount:
        log.warning(
            "subscription.credit.refused_amount_mismatch topup=%s captured=%s requested=%s",
            topup_id, captured_amount, topup.amount,
        )
        return CREDIT_REFUSED
    prior_status = topup.status
    credited = await sc.mark_topup_paid(
        db, topup, provider_ref=provider_ref, captured_amount=captured_amount,
    )
    if credited:
        await audit.record(
            db, action="subscription.payment", tenant_id=topup.tenant_id,
            entity_type="topup", entity_id=str(topup.id),
            after={"amount": int(topup.amount), "method": topup.method, "ref": provider_ref},
        )
    try:
        await db.commit()
    except IntegrityError:
        # (#116) Duplicate provider_ref the row lock didn't serialize → already
        # credited by the winning delivery. Idempotent no-op, not an error.
        await db.rollback()
        return CREDIT_ALREADY
    # Only when ``credited``: a losing concurrent delivery (CREDIT_ALREADY) changed
    # nothing and must not emit a second line for the same money.
    if credited:
        log.info(
            "payment.transition provider=subscription topup=%s tenant=%s from=%s to=paid",
            topup_id, topup.tenant_id, prior_status,
        )
    # mark_topup_paid returns False only when the row flipped to 'paid' between our
    # pre-check and its locked re-check (a concurrent winning delivery) → already.
    return CREDIT_OK if credited else CREDIT_ALREADY


async def _credit_by_topup_id(
    db: AsyncSession, topup_id: str, *, provider_ref: str | None,
    captured_amount: Decimal | None,
) -> bool:
    """Backward-compatible bool wrapper: True iff the balance was credited on THIS
    call. Refusals and idempotent no-ops both return False, matching the original
    contract (used by the #857/#116 amount-validation and idempotency suites)."""
    status = await _credit_by_topup_id_status(
        db, topup_id, provider_ref=provider_ref, captured_amount=captured_amount,
    )
    return status == CREDIT_OK


# ── Payme subscription JSON-RPC (#858) ───────────────────────────────────────

def _topup_id_from_account(account: Any) -> UUID | None:
    if not isinstance(account, dict):
        return None
    raw = account.get("topup_id") or account.get("topup")
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except (TypeError, ValueError):
        return None


def _sub_amount_ok(topup: TenantTopup, amount_tiyin: int) -> bool:
    """A top-up is a FIXED amount, so Payme's tiyin amount must match it exactly
    (1 UZS = 100 tiyin)."""
    if amount_tiyin <= 0:
        return False
    return amount_tiyin == int((topup.amount * 100).to_integral_value())


def _sub_state(topup: TenantTopup) -> int:
    pp = topup.provider_payload or {}
    if isinstance(pp.get("state"), int):
        return pp["state"]
    if topup.status == "paid":
        return STATE_COMPLETED
    if topup.status == "rejected":
        return STATE_CANCELLED
    return STATE_CREATED


async def _sub_check_perform(db: AsyncSession, rpc_id: Any, params: dict) -> dict:
    topup_id = _topup_id_from_account(params.get("account"))
    if topup_id is None:
        return _rpc_error(rpc_id, ERR_ACCOUNT_INVALID, "topup_id_required")
    # Narrow to the owning tenant before reading the row. An unknown id and a
    # nonexistent one produce the same answer below.
    if not await _scope_to_topup(db, topup_id):
        return _rpc_error(rpc_id, ERR_ACCOUNT_INVALID, "topup_not_found")
    topup = (await db.execute(
        select(TenantTopup).where(TenantTopup.id == topup_id)
    )).scalar_one_or_none()
    if topup is None:
        return _rpc_error(rpc_id, ERR_ACCOUNT_INVALID, "topup_not_found")
    try:
        amount_tiyin = int(params.get("amount", 0))
    except (TypeError, ValueError):
        return _rpc_error(rpc_id, ERR_AMOUNT_INVALID, "amount_invalid")
    if not _sub_amount_ok(topup, amount_tiyin):
        return _rpc_error(rpc_id, ERR_AMOUNT_INVALID, "amount_invalid")
    if topup.status != "pending":
        return _rpc_error(rpc_id, ERR_CANT_PERFORM, "topup_not_pending")
    return _rpc_result(rpc_id, {"allow": True})


async def _sub_create(db: AsyncSession, rpc_id: Any, params: dict) -> dict:
    topup_id = _topup_id_from_account(params.get("account"))
    if topup_id is None:
        return _rpc_error(rpc_id, ERR_ACCOUNT_INVALID, "topup_id_required")
    tx_id = str(params.get("id") or "")
    if not tx_id:
        return _rpc_error(rpc_id, ERR_TRANSACTION_NOT_FOUND, "transaction_id_required")
    if not await _scope_to_topup(db, topup_id):
        return _rpc_error(rpc_id, ERR_ACCOUNT_INVALID, "topup_not_found")
    time_ms = int(params.get("time") or _now_ms())
    try:
        amount_tiyin = int(params.get("amount", 0))
    except (TypeError, ValueError):
        return _rpc_error(rpc_id, ERR_AMOUNT_INVALID, "amount_invalid")

    # Idempotency: same Payme tx id → return the existing transaction.
    existing = (await db.execute(
        select(TenantTopup).where(TenantTopup.provider_ref == tx_id)
    )).scalar_one_or_none()
    if existing is not None:
        pp = existing.provider_payload or {}
        return _rpc_result(rpc_id, {
            "transaction": str(existing.id), "state": _sub_state(existing),
            "create_time": pp.get("create_time", time_ms),
        })

    topup = (await db.execute(
        select(TenantTopup).where(TenantTopup.id == topup_id).with_for_update()
    )).scalar_one_or_none()
    if topup is None:
        return _rpc_error(rpc_id, ERR_ACCOUNT_INVALID, "topup_not_found")
    # Already carries a (different) transaction, or already paid/cancelled → can't open.
    if topup.provider_ref or topup.status != "pending":
        return _rpc_error(rpc_id, ERR_CANT_PERFORM, "topup_unavailable")
    if not _sub_amount_ok(topup, amount_tiyin):
        return _rpc_error(rpc_id, ERR_AMOUNT_INVALID, "amount_invalid")

    topup.provider_ref = tx_id
    topup.provider_payload = {
        "state": STATE_CREATED, "create_time": time_ms, "amount_tiyin": amount_tiyin,
    }
    try:
        await db.commit()
    except IntegrityError:
        # Concurrent Create for the same tx_id raced us to the unique(provider_ref) index.
        await db.rollback()
        existing = (await db.execute(
            select(TenantTopup).where(TenantTopup.provider_ref == tx_id)
        )).scalar_one_or_none()
        if existing is not None:
            pp = existing.provider_payload or {}
            return _rpc_result(rpc_id, {
                "transaction": str(existing.id), "state": _sub_state(existing),
                "create_time": pp.get("create_time", time_ms),
            })
        raise
    return _rpc_result(rpc_id, {
        "transaction": str(topup.id), "state": STATE_CREATED, "create_time": time_ms,
    })


async def _sub_perform(db: AsyncSession, rpc_id: Any, params: dict) -> dict:
    tx_id = str(params.get("id") or "")
    # Narrow to the tenant that owns this provider transaction before reading it.
    if not await _scope_to_ref(db, tx_id):
        return _rpc_error(rpc_id, ERR_TRANSACTION_NOT_FOUND, "transaction_not_found")
    topup = (await db.execute(
        select(TenantTopup).where(TenantTopup.provider_ref == tx_id).with_for_update()
    )).scalar_one_or_none()
    if topup is None:
        return _rpc_error(rpc_id, ERR_TRANSACTION_NOT_FOUND, "transaction_not_found")
    pp = dict(topup.provider_payload or {})
    if topup.status == "paid":   # idempotent — return the recorded perform_time
        return _rpc_result(rpc_id, {
            "transaction": str(topup.id), "state": STATE_COMPLETED,
            "perform_time": pp.get("perform_time", _now_ms()),
        })
    if topup.status == "rejected":
        return _rpc_error(rpc_id, ERR_CANT_PERFORM, "transaction_cancelled")

    # Credit the balance via the locked, idempotent, amount-checked helper. The amount
    # was frozen + validated at CreateTransaction, so captured == topup.amount.
    credited = await sc.mark_topup_paid(
        db, topup, provider_ref=tx_id, captured_amount=topup.amount,
    )
    if not credited:
        await db.rollback()
        return _rpc_error(rpc_id, ERR_CANT_PERFORM, "perform_failed")
    now_ms = _now_ms()
    topup.provider_payload = {**pp, "state": STATE_COMPLETED, "perform_time": now_ms}
    await audit.record(
        db, action="subscription.payment", tenant_id=topup.tenant_id,
        entity_type="topup", entity_id=str(topup.id),
        after={"amount": int(topup.amount), "method": topup.method, "ref": tx_id},
    )
    await db.commit()
    return _rpc_result(rpc_id, {
        "transaction": str(topup.id), "state": STATE_COMPLETED, "perform_time": now_ms,
    })


async def _sub_cancel(db: AsyncSession, rpc_id: Any, params: dict) -> dict:
    tx_id = str(params.get("id") or "")
    # Narrow to the tenant that owns this provider transaction before reading it.
    if not await _scope_to_ref(db, tx_id):
        return _rpc_error(rpc_id, ERR_TRANSACTION_NOT_FOUND, "transaction_not_found")
    topup = (await db.execute(
        select(TenantTopup).where(TenantTopup.provider_ref == tx_id).with_for_update()
    )).scalar_one_or_none()
    if topup is None:
        return _rpc_error(rpc_id, ERR_TRANSACTION_NOT_FOUND, "transaction_not_found")
    pp = dict(topup.provider_payload or {})
    if topup.status == "rejected":   # idempotent
        state = STATE_CANCELLED_AFTER_COMPLETE if pp.get("was_completed") else STATE_CANCELLED
        return _rpc_result(rpc_id, {
            "transaction": str(topup.id), "state": state,
            "cancel_time": pp.get("cancel_time", _now_ms()),
        })

    was_completed = topup.status == "paid"
    now_ms = _now_ms()
    state = STATE_CANCELLED_AFTER_COMPLETE if was_completed else STATE_CANCELLED
    # (F-03) Reverse the credit and finalise the cancel atomically, wrapped so a DB
    # CHECK violation (or any failure) becomes a proper Payme RPC error instead of an
    # uncaught 500 — Payme retries cancels aggressively, and a 500 loop both wedges
    # the tx forever and keeps refundable money on the platform.
    try:
        if was_completed:
            # Reverse the balance credit (lock the tenant row). The topped-up funds may
            # already have settled invoices (mark_topup_paid settles immediately), so the
            # full reversal can exceed the current balance. The `balance >= 0` /
            # `balance_after >= 0` CHECKs forbid a negative balance, so we clamp the
            # balance at zero and book the un-coverable remainder as an explicit
            # receivable ledger entry — the platform still owes that amount, and the
            # books stay balanced without a schema change (no negative-balance column).
            tenant = (await db.execute(
                select(Tenant).where(Tenant.id == topup.tenant_id).with_for_update()
            )).scalar_one_or_none()
            if tenant is not None:
                current = tenant.balance or Decimal(0)
                coverable = current if current < topup.amount else topup.amount
                shortfall = topup.amount - coverable
                new_balance = round_amount(current - coverable)   # ≥ 0 by construction
                tenant.balance = new_balance
                # The part actually debited from the prepaid balance.
                db.add(TenantBalanceTxn(
                    tenant_id=tenant.id, amount=-coverable, kind="topup_reversal",
                    note=f"Payme cancel · {tx_id}", balance_after=new_balance,
                    created_by_user_id=None,
                ))
                # The part that couldn't be debited (funds already spent) is recorded as
                # a receivable the tenant owes the platform. balance_after stays at the
                # clamped balance (this entry doesn't move the balance), satisfying the
                # CHECK while keeping a clear audit trail for reconciliation.
                if shortfall > 0:
                    db.add(TenantBalanceTxn(
                        tenant_id=tenant.id, amount=-shortfall, kind="topup_reversal_shortfall",
                        note=f"Payme cancel receivable · {tx_id}", balance_after=new_balance,
                        created_by_user_id=None,
                    ))
                    log.warning(
                        "subscription.payme.cancel_shortfall tenant=%s topup=%s "
                        "reversed=%s clamped=%s receivable=%s",
                        tenant.id, topup.id, topup.amount, coverable, shortfall,
                    )
        topup.status = "rejected"
        topup.provider_payload = {
            **pp, "state": state, "cancel_time": now_ms,
            "was_completed": was_completed, "cancel_reason": params.get("reason"),
        }
        await db.commit()
    except IntegrityError:
        # A balance CHECK (or unique) constraint rejected the reversal. Roll back and
        # return ERR_CANT_CANCEL so Payme records a clean RPC error and stops hammering
        # the endpoint with a 500 loop; the tx stays cancellable on a later retry.
        await db.rollback()
        log.exception("subscription.payme.cancel_integrity_error topup=%s tx=%s", topup.id, tx_id)
        record_webhook("subscription", "error")
        return _rpc_error(rpc_id, ERR_CANT_CANCEL, "cancel_failed")
    return _rpc_result(rpc_id, {
        "transaction": str(topup.id), "state": state, "cancel_time": now_ms,
    })


async def _sub_check_transaction(db: AsyncSession, rpc_id: Any, params: dict) -> dict:
    tx_id = str(params.get("id") or "")
    # Narrow to the tenant that owns this provider transaction before reading it.
    if not await _scope_to_ref(db, tx_id):
        return _rpc_error(rpc_id, ERR_TRANSACTION_NOT_FOUND, "transaction_not_found")
    topup = (await db.execute(
        select(TenantTopup).where(TenantTopup.provider_ref == tx_id)
    )).scalar_one_or_none()
    if topup is None:
        return _rpc_error(rpc_id, ERR_TRANSACTION_NOT_FOUND, "transaction_not_found")
    pp = topup.provider_payload or {}
    return _rpc_result(rpc_id, {
        "transaction": str(topup.id), "state": _sub_state(topup),
        "create_time": pp.get("create_time", 0),
        "perform_time": pp.get("perform_time", 0),
        "cancel_time": pp.get("cancel_time", 0),
        "reason": pp.get("cancel_reason"),
    })


@router.post("/payme", dependencies=[webhook_limit()])
async def payme_subscription(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    """Payme platform-subscription JSON-RPC endpoint (#858) — full merchant protocol.

    AUTH (#64): HTTP Basic ``Paycom:<platform_payme_secret>`` against the secret in the
    ``billing_providers`` setting, constant-time. No platform secret configured → 503
    (route not wired), so an unconfigured webhook can never move money.
    """
    secret = await _provider_secret(db, "payme")
    if not secret:
        return _payme_err(None, "subscription_payme_not_configured", http=503)

    body = await _safe_json(request)
    rpc_id = body.get("id")
    supplied = _parse_basic(authorization)
    if not supplied or not hmac.compare_digest(supplied, secret):
        log.warning("subscription.payme.auth_failed")
        record_webhook("subscription", "auth_failed")
        return _rpc_error(rpc_id, ERR_AUTH, "auth_failed")

    method = body.get("method")
    params = body.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    handlers = {
        "CheckPerformTransaction": _sub_check_perform,
        "CreateTransaction": _sub_create,
        "PerformTransaction": _sub_perform,
        "CancelTransaction": _sub_cancel,
        "CheckTransaction": _sub_check_transaction,
    }
    handler = handlers.get(method) if isinstance(method, str) else None
    if handler is None:
        record_webhook("subscription", "error")
        return _rpc_error(rpc_id, ERR_METHOD_NOT_FOUND, "method_not_found")
    # (H6) Mirror payme.py's crash guard: a handler exception must become a
    # structured JSON-RPC error, not a raw HTTP 500 — Payme retries a non-2xx /
    # non-JSON-RPC reply aggressively.
    try:
        resp = await handler(db, rpc_id, params)
    except Exception:
        log.exception("subscription.payme.handler_crashed method=%s", method)
        await db.rollback()
        record_webhook("subscription", "error")
        return _rpc_error(rpc_id, ERR_INTERNAL, "internal_error")
    record_webhook("subscription", "error" if "error" in resp else "ok")
    return resp


@router.post("/click", dependencies=[webhook_limit()])
async def click_subscription(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Click platform-subscription callback.

    AUTH (#64): the MD5 ``sign_string`` is recomputed from the request fields + the
    platform Click ``secret_key`` (from ``billing_providers``) and compared constant-time,
    exactly as per-tenant ``click.py`` does. No secret configured → 503. Click sends
    ``action`` 0 (Prepare) then 1 (Complete); we credit on Complete using
    ``merchant_trans_id`` (= our ``topup_id``)."""
    secret = await _provider_secret(db, "click")
    if not secret:
        return {"error": -8, "error_note": "subscription_click_not_configured"}

    form = await request.form()
    action = str(form.get("action", ""))
    service_id = str(form.get("service_id", ""))
    merchant_trans_id = str(form.get("merchant_trans_id", ""))
    expected = _click_sign(
        click_trans_id=str(form.get("click_trans_id", "")),
        service_id=service_id,
        secret_key=secret,
        merchant_trans_id=merchant_trans_id,
        # merchant_prepare_id is part of the signature only on Complete (action 1).
        merchant_prepare_id=str(form.get("merchant_prepare_id", "")) if action == "1" else None,
        amount=str(form.get("amount", "")),
        action=action,
        sign_time=str(form.get("sign_time", "")),
    )
    supplied_sign = str(form.get("sign_string", ""))
    if not supplied_sign or not hmac.compare_digest(supplied_sign, expected):
        log.warning("subscription.click.sign_check_failed")
        return {"error": -1, "error_note": "SIGN CHECK FAILED"}

    if action == "1" and merchant_trans_id:   # Complete
        # (H6) A crash here must become a structured Click error, not a raw
        # HTTP 500 — same guard as the per-tenant click.py handlers.
        try:
            status = await _credit_by_topup_id_status(
                db, merchant_trans_id, provider_ref=str(form.get("click_trans_id") or ""),
                captured_amount=_amount_or_mismatch(form.get("amount")),
            )
        except Exception:
            log.exception(
                "subscription.click.handler_crashed trans=%s topup=%s",
                form.get("click_trans_id"), merchant_trans_id,
            )
            await db.rollback()
            record_webhook("subscription", "error")
            return {"error": CLICK_ERR_INTERNAL, "error_note": "internal_error"}
        if status == CREDIT_REFUSED:
            # (F-10) The platform received money but could NOT credit it (missing/short/
            # unparseable amount, or unknown top-up). Returning success here would tell
            # Click to stop retrying, stranding the funds with no balance credit and no
            # audit row. Surface a non-zero Click error so Click retries, and log+alert
            # for manual reconciliation. ERR_BAD_AMOUNT (-2) is Click's amount-fault code
            # and covers the dominant refusal cause (amount missing/mismatch).
            log.error(
                "subscription.click.credit_refused trans=%s topup=%s amount=%s — "
                "received-but-uncredited, needs reconciliation",
                form.get("click_trans_id"), merchant_trans_id, form.get("amount"),
            )
            record_webhook("subscription", "error")
            return {"error": CLICK_ERR_BAD_AMOUNT, "error_note": "credit_refused"}
        # CREDIT_OK / CREDIT_ALREADY → the balance is credited exactly once; tell Click
        # success so it stops retrying (idempotent redeliveries land here too).
        record_webhook("subscription", "ok")
    # Click expects error/error_note; 0 = success.
    return {"error": CLICK_ERR_OK, "error_note": "Success"}


async def _safe_json(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def _payme_err(rpc_id: Any, message: str, *, http: int = 200) -> Any:
    from fastapi.responses import ORJSONResponse
    return ORJSONResponse(
        status_code=http,
        content={"jsonrpc": "2.0", "id": rpc_id, "error": {"code": ERR_AUTH, "message": message}},
    )
