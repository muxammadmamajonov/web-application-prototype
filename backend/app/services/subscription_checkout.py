"""Subscription checkout helpers (Round 37).

The platform receives subscription top-ups from tenants in three ways:

* ``card``  — tenant transfers to one of the platform's receiving cards
              (``billing_cards`` setting) and uploads a receipt; a platform
              admin approves it, crediting the balance.
* ``payme`` — Payme (paycom.uz) hosted checkout.
* ``click`` — Click (click.uz) hosted checkout.

Provider credentials live in the ``billing_providers`` platform setting and are
meant to be filled in by the operator later ("plug creds later"). Until then
``providers_enabled`` reports the online options as off and only the card flow
is offered. The webhook handlers credit the balance when a provider confirms a
payment; signature verification is intentionally minimal and must be hardened
once real credentials and the provider's exact callback contract are wired up.
"""
from __future__ import annotations

import base64
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PlatformSetting, TenantBalanceTxn, TenantTopup

BILLING_PROVIDERS_KEY = "billing_providers"
BILLING_CARDS_KEY = "billing_cards"
PAYMENT_TERMS_KEY = "payment_terms"


async def _get_setting(db: AsyncSession, key: str) -> Any:
    row = (await db.execute(
        select(PlatformSetting).where(PlatformSetting.key == key)
    )).scalar_one_or_none()
    return row.value if row else None


async def get_providers(db: AsyncSession) -> dict[str, Any]:
    """Return the raw ``billing_providers`` config, or an empty default."""
    val = await _get_setting(db, BILLING_PROVIDERS_KEY)
    if isinstance(val, dict):
        return val
    return {}


async def get_billing_cards(db: AsyncSession) -> list[dict[str, Any]]:
    val = await _get_setting(db, BILLING_CARDS_KEY)
    if isinstance(val, dict) and isinstance(val.get("items"), list):
        return val["items"]
    if isinstance(val, list):
        return val
    return []


async def get_payment_terms(db: AsyncSession) -> dict[str, str]:
    val = await _get_setting(db, PAYMENT_TERMS_KEY)
    if isinstance(val, dict):
        return {"uz": str(val.get("uz") or ""), "ru": str(val.get("ru") or "")}
    return {"uz": "", "ru": ""}


def _provider_enabled(providers: dict[str, Any], name: str) -> bool:
    p = providers.get(name)
    return bool(isinstance(p, dict) and p.get("enabled") and p.get("merchant_id"))


async def providers_enabled(db: AsyncSession) -> dict[str, bool]:
    p = await get_providers(db)
    return {"payme": _provider_enabled(p, "payme"), "click": _provider_enabled(p, "click")}


def build_payme_url(merchant_id: str, amount_soum: int, topup_id: str) -> str:
    """Payme hosted-checkout URL. Amount is sent in tiyin (so'm × 100).  # noqa: RUF002

    The merchant-side ``account`` field is ``topup_id`` so the webhook can map
    the payment back to the originating top-up row.
    """
    raw = f"m={merchant_id};ac.topup_id={topup_id};a={amount_soum * 100}"
    token = base64.b64encode(raw.encode()).decode()
    return f"https://checkout.paycom.uz/{token}"


def build_click_url(service_id: str, merchant_id: str, amount_soum: int, topup_id: str) -> str:
    """Click hosted-checkout URL. ``transaction_param`` carries the top-up id."""
    return (
        "https://my.click.uz/services/pay"
        f"?service_id={service_id}&merchant_id={merchant_id}"
        f"&amount={amount_soum}&transaction_param={topup_id}"
    )


async def mark_topup_paid(
    db: AsyncSession, topup: TenantTopup, *,
    reviewed_by_user_id: UUID | None = None, provider_ref: str | None = None,
    captured_amount: Decimal | None = None,
) -> bool:
    """Credit the tenant's balance for a top-up and mark it paid.

    Concurrency- and money-safe (#116): the top-up row is locked ``FOR UPDATE``
    and its status re-checked under the lock — an in-process status check is not
    enough when a provider retries the callback. The tenant row is also locked for
    the balance mutation so concurrent top-ups don't lose an update. When the
    provider reports a ``captured_amount`` we credit ONLY if it matches the amount
    the tenant requested (a tenant must not request a large top-up and pay less).
    Idempotent: an already-``paid`` top-up is a no-op. The caller commits.
    """
    from app.models import Tenant

    # Lock this top-up row; concurrent provider deliveries serialize here and the
    # second one sees status='paid' after the first commits.
    await db.refresh(topup, with_for_update=True)
    if topup.status == "paid":
        return False
    if captured_amount is not None and captured_amount != topup.amount:
        return False

    tenant = (await db.execute(
        select(Tenant).where(Tenant.id == topup.tenant_id).with_for_update()
    )).scalar_one_or_none()
    if tenant is None:
        return False

    tenant.balance = (tenant.balance or 0) + topup.amount
    db.add(TenantBalanceTxn(
        tenant_id=tenant.id, amount=topup.amount, kind="topup",
        note=f"Balans to'ldirish · {topup.method}", balance_after=tenant.balance,
        created_by_user_id=reviewed_by_user_id,
    ))
    topup.status = "paid"
    topup.reviewed_by_user_id = reviewed_by_user_id
    topup.reviewed_at = datetime.now(UTC)
    if provider_ref:
        topup.provider_ref = provider_ref
    # (#246) Apply the freshly-credited balance to the tenant's open invoices so paying
    # online actually clears them (and stops dunning). Idempotent — no-op once covered.
    from app.services.billing import settle_invoices_from_balance
    await settle_invoices_from_balance(db, tenant)
    return True


async def build_checkout_url(db: AsyncSession, *, method: str, amount_soum: int, topup_id: str) -> str | None:
    """Construct the hosted-checkout URL for an online method, or None if that
    provider isn't configured."""
    providers = await get_providers(db)
    if method == "payme" and _provider_enabled(providers, "payme"):
        cfg = providers["payme"]
        return build_payme_url(str(cfg["merchant_id"]), amount_soum, topup_id)
    if method == "click" and _provider_enabled(providers, "click"):
        cfg = providers["click"]
        return build_click_url(
            str(cfg.get("service_id") or ""), str(cfg["merchant_id"]), amount_soum, topup_id,
        )
    return None
