"""Tenant back-office: the shop's own subscription — invoices, plan changes and
balance top-ups — and its stock ledger."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money_utils import round_amount
from app.core.security import current_user
from app.db import get_db
from app.models import (
    Invoice,
    Product,
    StockMovement,
    Tenant,
    TenantUser,
    User,
)
from app.models.enums import (
    InvoiceStatus,
    StockMovementKind,
    TenantStatus,
)
from app.schemas.common import LimitQ, OffsetQ
from app.services.tenant_status import may_self_clear_block

from ._shared import (
    _require_owner,
    _require_owner_or_manager,
    resolve_tenant,
)

router = APIRouter()

# ── billing (Round 27) ───────────────────────────────────────────────────────

class PlanChangeRequest(BaseModel):
    # Free-form (R36); the handler validates it against an active plan row.
    plan_code: str = Field(min_length=1, max_length=32)

def _invoice_dict(inv: Invoice) -> dict:
    return {
        "id": str(inv.id),
        "plan_code": inv.plan_code,
        "period_start": inv.period_start.isoformat(),
        "period_end": inv.period_end.isoformat(),
        "active_installments_count": inv.active_installments_count,
        "unit_price": str(inv.unit_price) if inv.unit_price is not None else None,
        "amount": str(inv.amount),
        "status": inv.status.value,
        "line_items": inv.line_items,
        "issued_at": inv.issued_at.isoformat() if inv.issued_at else None,
        "due_at": inv.due_at.isoformat() if inv.due_at else None,
        "paid_at": inv.paid_at.isoformat() if inv.paid_at else None,
        "payment_method": inv.payment_method,
    }

@router.get("/billing")
async def tenant_billing(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Current plan, live usage, projected charge, and any upsell nudge.

    Owner/manager only: this is the shop's commercial relationship with the
    platform, gated the same way as the billing mutations and `/billing/topups`.
    """
    from app.models import Invoice
    from app.services import billing
    from app.services.plan_limits import effective_config

    tenant, membership = ctx
    _require_owner_or_manager(membership)
    active_count = await billing.active_installment_count(db, tenant.id)
    cfg = await effective_config(db, tenant)

    projected = await billing.projected_monthly(db, tenant)

    last_invoice = (await db.execute(
        select(Invoice).where(Invoice.tenant_id == tenant.id)
        .order_by(Invoice.issued_at.desc()).limit(1)
    )).scalar_one_or_none()

    return {
        "plan_code": tenant.plan_code,
        "status": tenant.status.value,
        "trial_ends_at": tenant.trial_ends_at.isoformat() if tenant.trial_ends_at else None,
        "active_installments": active_count,
        "limits": cfg.limits,
        "overage": cfg.overage,
        "base_price": cfg.price_monthly,
        "projected": projected,
        "suggestion": await billing.suggest_upgrade(db, tenant),
        "last_invoice": _invoice_dict(last_invoice) if last_invoice else None,
    }

@router.get("/invoices")
async def tenant_invoices(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: LimitQ = 50,
    offset: OffsetQ = 0,
) -> list[dict]:
    """Invoice history, newest first (#78: bounded). Owner/manager only."""
    from app.models import Invoice

    tenant, membership = ctx
    _require_owner_or_manager(membership)
    rows = (await db.execute(
        select(Invoice).where(Invoice.tenant_id == tenant.id)
        .order_by(Invoice.issued_at.desc(), Invoice.id.desc())
        .offset(offset).limit(limit)
    )).scalars().all()
    return [_invoice_dict(i) for i in rows]

@router.post("/plan-change")
async def tenant_plan_change(
    body: PlanChangeRequest,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Switch the tenant's plan (owner only). A trial tenant becomes active."""
    from app.models import Plan
    from app.services import audit

    tenant, membership = ctx
    _require_owner(membership)

    new_code = body.plan_code
    target = (await db.execute(
        select(Plan).where(Plan.code == new_code, Plan.is_active.is_(True))
    )).scalar_one_or_none()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "plan_not_found")
    # Contact-sales plans (e.g. Enterprise) can never be self-selected.
    if target.is_contact_sales:
        raise HTTPException(status_code=403, detail={"error": "contact_sales"})

    # A paid plan is charged from the prepaid balance. If there isn't enough,
    # the owner must top up first (402 → the UI shows balance + shortfall).
    # (F-11) Money is Numeric(14,2) — compare and debit in Decimal via round_amount;
    # int() would truncate a 1000.50 plan to 1000.00 (revenue leak) and read a 999.99
    # balance as 999 (a spurious 402). 402 amounts are reported as Decimal strings.
    price = round_amount(target.price_monthly or 0)
    from app.services.billing import _period_bounds
    now = datetime.now(UTC)
    period_start, period_end = _period_bounds(now.date())
    # Serialize period inspection + charge + invoice creation. Without taking
    # this lock first, two plan-change requests can both observe "no invoice"
    # before either inserts it and the loser ends in a unique-constraint 500.
    await db.refresh(tenant, with_for_update=True)
    existing_period_invoice = (await db.execute(
        select(Invoice.id).where(
            Invoice.tenant_id == tenant.id,
            Invoice.period_start == period_start,
        )
    )).scalar_one_or_none()
    # The period is billed once. A mid-period plan switch updates entitlement
    # now and the next monthly cycle bills the new plan; it never debits a
    # second full month on top of an invoice already issued for this period.
    charge = price if existing_period_invoice is None else Decimal("0")
    if charge > 0:
        balance = round_amount(tenant.balance or 0)
        if balance < charge:
            raise HTTPException(
                status.HTTP_402_PAYMENT_REQUIRED,
                detail={"error": "insufficient_balance", "required": str(charge),
                        "balance": str(balance), "shortfall": str(charge - balance)},
            )

    prev = tenant.plan_code
    tenant.plan_code = new_code
    # Choosing a paid plan clears the trial/suspension lifecycle.
    #
    # (#MUDD-54 R1) `blocked` is lifted here too — but conditionally, because the
    # status conflates two different locks. A trial-expiry block owes nothing, so
    # settle_invoices_from_balance can never fire for it and plan-change is its
    # ONLY exit; a debt block and an operator block must not be clearable by the
    # shop itself. `may_self_clear_block` is where that distinction is drawn and
    # argued — read it before widening this branch.
    lift_blocked = (
        tenant.status is TenantStatus.blocked
        and await may_self_clear_block(db, tenant)
    )
    if lift_blocked or tenant.status in (TenantStatus.demo, TenantStatus.suspended):
        tenant.status = TenantStatus.active
        tenant.trial_ends_at = None
        tenant.suspended_at = None

    if charge > 0:
        from app.models import TenantBalanceTxn
        tenant.balance = round_amount(tenant.balance or Decimal(0)) - charge
        db.add(TenantBalanceTxn(
            tenant_id=tenant.id, amount=-charge, kind="plan_charge",
            note=f"{new_code} · 1 oy", balance_after=tenant.balance,
            created_by_user_id=membership.user_id,
        ))

    if existing_period_invoice is None:
        from app.services.billing import next_invoice_number
        db.add(Invoice(
            tenant_id=tenant.id,
            plan_code=new_code,
            invoice_number=await next_invoice_number(db, now.year),
            period_start=period_start,
            period_end=period_end,
            amount=price,
            status=InvoiceStatus.paid,
            line_items={
                "lines": [{
                    "label": f"{target.name_uz} — oylik obuna",
                    "qty": 1, "unit_price": str(price), "total": str(price),
                }],
                "model": "subscription",
            },
            issued_at=now,
            due_at=now,
            paid_at=now,
            payment_method="balance" if price > 0 else "free",
        ))

    await audit.record(
        db, action="tenant.plan_change", tenant_id=tenant.id,
        actor_user_id=membership.user_id, entity_type="tenant",
        entity_id=str(tenant.id), after={"from": prev, "to": new_code, "charged": str(charge)},
    )
    await db.commit()
    # NO `db.refresh(tenant)` here. Under RLS the commit above ends the
    # transaction, and the next statement runs in a fresh one as the restricted
    # `muddato_app` role — which cannot SELECT the `tenants` row the way
    # `resolve_tenant` could (it loaded it as `postgres`, before the scope was
    # pinned). The refresh then finds no row and SQLAlchemy raises
    # "Could not refresh instance '<Tenant>'", surfacing to the user as
    # "Temporary server error". It is also unnecessary: the session is
    # `expire_on_commit=False`, so `tenant` still holds every value this
    # handler set. Observed in production 2026-08-06.
    return {"plan_code": tenant.plan_code, "status": tenant.status.value, "balance": str(round_amount(tenant.balance or 0))}

# ── balance top-up / online subscription checkout (Round 37) ─────────────────

@router.get("/billing/topup-info")
async def tenant_topup_info(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Everything the tenant needs to top up: balance, payment policy, the
    platform's receiving cards, and which online providers are live.

    Owner/manager only — it feeds the same screen as `POST /billing/topup`.
    """
    from app.core.card_networks import PLATFORM_BALANCE_CURRENCY, compatible_cards
    from app.models.enums import TenantRole
    from app.services import subscription_checkout as sc

    tenant, membership = ctx
    _require_owner_or_manager(membership)
    # Only cards that can actually settle the balance's currency. A card the
    # shop cannot transfer to must not reach the screen at all: it is copied
    # into a banking app, so showing it disabled still invites a real transfer.
    all_cards = await sc.get_billing_cards(db)
    offerable = compatible_cards(all_cards, PLATFORM_BALANCE_CURRENCY)
    return {
        "balance": int(tenant.balance or 0),
        "currency": PLATFORM_BALANCE_CURRENCY,
        "terms": await sc.get_payment_terms(db),
        "cards": offerable,
        # "cards exist but none can take this currency" is a different
        # situation from "no cards are configured", and the shop needs to be told
        # which — one is us mid-setup, the other is a misconfiguration support has
        # to fix. Carries a boolean, never the withheld cards.
        "cards_withheld": bool(all_cards) and not offerable,
        "providers": await sc.providers_enabled(db),
        # (#9) Who may actually top up, decided by the SERVER against the real
        # membership. The client used to derive this from its own session store
        # (`activeRole() === 'owner'`), which returns null whenever the active
        # tenant id does not match a membership — and then hid the top-up
        # control with no explanation, for a genuine owner. `POST /billing/topup`
        # is owner-only, so this is the same rule, stated once, by the side that
        # enforces it.
        "can_topup": membership.role == TenantRole.owner,
    }

@router.get("/billing/topups")
async def tenant_topups(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: OffsetQ = 0,
) -> list[dict]:
    """This tenant's top-up requests, newest first.

    Owner/manager only: this is the shop's billing history, and the sibling billing
    mutations are already gated the same way. Hiding the screen in the UI is not an
    access control — a sales-role member could otherwise read it with a direct call.
    """
    from app.models import TenantTopup

    tenant, membership = ctx
    _require_owner_or_manager(membership)
    rows = (await db.execute(
        select(TenantTopup).where(TenantTopup.tenant_id == tenant.id)
        .order_by(TenantTopup.created_at.desc(), TenantTopup.id.desc())
        .offset(offset).limit(50)
    )).scalars().all()
    return [{
        "id": str(r.id), "amount": int(r.amount), "method": r.method, "status": r.status,
        "dest_card_label": r.dest_card_label,
        "created_at": r.created_at.isoformat(),
    } for r in rows]

@router.post("/billing/topup", status_code=status.HTTP_201_CREATED)
async def tenant_create_topup(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    amount: Annotated[int, Form()],
    method: Annotated[Literal["card", "payme", "click"], Form()],
    card_id: Annotated[str | None, Form()] = None,
    receipt: Annotated[UploadFile | None, File()] = None,
) -> dict:
    """Create a pending balance top-up.

    * ``card``  — a manual transfer; a receipt photo is required and a platform
      admin approves it later (credits the balance).
    * ``payme`` / ``click`` — returns a hosted-checkout ``checkout_url`` (or 400
      ``provider_not_configured`` until the operator plugs in credentials). The
      balance is credited by the provider webhook on success.
    """
    from app.models import TenantTopup
    from app.services import subscription_checkout as sc
    from app.services.media import store_payment_proof

    tenant, membership = ctx
    _require_owner(membership)
    if amount <= 0 or amount > 100_000_000:   # (#200) sane single-top-up cap (~$8k)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_amount")

    receipt_url: str | None = None
    dest_card_id: str | None = None
    dest_card_label: str | None = None
    if method == "card":
        if receipt is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "receipt_required")
        # The owner picks WHICH receiving card they transferred to, so the
        # approving admin can match the transaction to a card.
        if not card_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "card_required")
        from app.core.card_networks import (
            PLATFORM_BALANCE_CURRENCY,
            last4,
            rejection_code,
            resolve_card,
        )

        cards = await sc.get_billing_cards(db)
        card = next((c for c in cards if str(c.get("id")) == str(card_id)), None)
        if card is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "card_not_found")
        # Re-checked here, not merely filtered in the list endpoint: the id
        # arrives from the client, so an incompatible card is one crafted
        # request away if only the UI enforces this.
        if (code := rejection_code(card, PLATFORM_BALANCE_CURRENCY)) is not None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, code)
        dest_card_id = str(card_id)
        _last4 = last4(card)
        # Denormalised snapshot so the record survives later edits to the card
        # list. Network and settlement currency ride along so a receipt reviewed
        # months later still says which rail the money took, even if the card
        # has since been re-pointed or removed. Last four digits only — a
        # top-up row is read by support staff and exported.
        _resolved = resolve_card(card)
        dest_card_label = " · ".join(
            p for p in [
                _resolved["network"].value if _resolved["network"] else None,
                card.get("bank"),
                (f"···{_last4}" if _last4 else None),
                _resolved["settlement_currency"],
            ] if p
        ) or dest_card_id
        from app.core.limits import MAX_UPLOAD_BYTES, read_capped, receipt_type_ok
        # (#9) Only PDF/PNG/JPG/JPEG. The picker already refuses anything else,
        # but `accept` is a hint, not a gate — and this file is opened by hand
        # by whichever admin reviews the transfer.
        if not receipt_type_ok(receipt.filename, receipt.content_type):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "receipt_bad_type")
        content = await read_capped(receipt, MAX_UPLOAD_BYTES)  # (#88)
        # (#115) Bank-transfer receipts hold amounts/names — store privately, not
        # on public archive.org. (#820) Routed through the shared payment-proof gate,
        # so a top-up slip lands in the tenant's Telegram topic when that's enabled —
        # exactly like the customer cash-receipt path, no longer diverging.
        receipt_url = await store_payment_proof(
            db, content, receipt.content_type, tenant_id=tenant.id,
            entity_type="tenant_topup", uploaded_by=membership.user_id,
            # (#817) Idempotent on retry: same amount + method + card + slip → same
            # key, so a double-submit reuses the row instead of re-sending.
            idempotency_discriminator=f"{amount}|{method}|{dest_card_id}",
        )

    topup = TenantTopup(
        tenant_id=tenant.id, amount=amount, method=method, status="pending",
        receipt_url=receipt_url, dest_card_id=dest_card_id, dest_card_label=dest_card_label,
        created_by_user_id=membership.user_id,
    )
    db.add(topup)
    await db.flush()   # need topup.id for the checkout account param

    checkout_url: str | None = None
    if method in ("payme", "click"):
        checkout_url = await sc.build_checkout_url(
            db, method=method, amount_soum=amount, topup_id=str(topup.id),
        )
        if checkout_url is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "provider_not_configured")

    await db.commit()
    await db.refresh(topup)
    return {"id": str(topup.id), "status": topup.status, "checkout_url": checkout_url}

# ── inventory / stock movements (Round 28) ──────────────────────────────────

class StockAdjustRequest(BaseModel):
    qty: int = Field(description="Signed quantity; sign is derived from kind if positive")
    kind: Literal["in", "out", "return", "writeoff", "inventory"]
    reason: str | None = Field(default=None, max_length=500)

def _movement_dict(mv: StockMovement) -> dict:
    return {
        "id": str(mv.id),
        "product_id": str(mv.product_id),
        "qty": mv.qty,
        "kind": mv.kind.value,
        "reason": mv.reason,
        "by_user_id": str(mv.by_user_id) if mv.by_user_id else None,
        "related_installment_id": (
            str(mv.related_installment_id) if mv.related_installment_id else None
        ),
        "created_at": mv.created_at.isoformat() if mv.created_at else None,
    }

@router.get("/products/{pid}/movements")
async def product_movements(
    pid: UUID,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    """Stock movement timeline for a product, newest first."""
    tenant, _ = ctx
    product = (await db.execute(
        select(Product).where(Product.id == pid, Product.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product_not_found")
    rows = (await db.execute(
        select(StockMovement).where(
            StockMovement.product_id == pid, StockMovement.tenant_id == tenant.id,
        ).order_by(StockMovement.created_at.desc()).limit(200)
    )).scalars().all()
    return [_movement_dict(m) for m in rows]

@router.post("/products/{pid}/stock-adjust", status_code=status.HTTP_201_CREATED)
async def adjust_stock(
    pid: UUID,
    body: StockAdjustRequest,
    user: Annotated[User, Depends(current_user)],
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Manual stock change (owner/manager). ``out``/``writeoff`` go negative."""
    from app.services import inventory

    tenant, membership = ctx
    _require_owner_or_manager(membership)
    product = (await db.execute(
        select(Product).where(Product.id == pid, Product.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product_not_found")

    kind = StockMovementKind(body.kind)
    magnitude = abs(body.qty)
    if kind in (StockMovementKind.out, StockMovementKind.writeoff):
        signed = -magnitude
    elif kind == StockMovementKind.inventory:
        # Treat qty as the counted on-hand total → store the delta.
        signed = body.qty - (product.stock or 0)
    else:  # in, return
        signed = magnitude

    mv = await inventory.record_movement(
        db, tenant_id=tenant.id, product_id=pid, qty=signed, kind=kind,
        reason=body.reason, by_user_id=user.id,
    )
    await db.commit()
    await db.refresh(mv)
    await db.refresh(product)
    return {"movement": _movement_dict(mv), "stock": product.stock}

@router.get("/inventory/low-stock")
async def low_stock(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    """Products at/below their min_stock_level."""
    from app.services import inventory

    tenant, _ = ctx
    rows = await inventory.low_stock_products(db, tenant.id)
    return [{
        "id": str(p.id),
        "name_uz": p.name_uz,
        "sku": p.sku,
        "stock": p.stock,
        "min_stock_level": p.min_stock_level,
    } for p in rows]
