"""Platform HQ: tenant lifecycle — creation, owner invites, deletion, detail — and
the balance top-ups that fund a tenant's subscription."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core import invite_codes
from app.core.currency import (
    ints_by_currency,
    money_by_currency,
    primary_total,
    tenant_primary_currency,
)
from app.core.money_utils import round_amount
from app.core.ratelimit import invite_limit
from app.core.security import current_user
from app.db import get_db
from app.models import (
    Customer,
    Guarantor,
    Installment,
    Invitation,
    Payment,
    Product,
    Tenant,
    TenantBalanceTxn,
    TenantSubscription,
    TenantTopup,
    TenantUser,
    User,
)
from app.models.enums import (
    InstallmentStatus,
    PaymentStatus,
    TenantStatus,
)
from app.schemas.common import OffsetQ

from ._shared import (
    OWNER_INVITE_HOURS,
    _display_name,
    _owner_invite_url,
    _require_platform,
    _require_super,
)

router = APIRouter()

async def _mint_owner_invite(db: AsyncSession, *, tenant_id, actor_user_id) -> str:
    """Create a one-shot owner invitation and return its share URL.
    Any previous unused owner invites for this tenant are invalidated so only
    the latest link works."""

    # Invalidate older, still-open owner invites for this tenant.
    stale = (await db.execute(
        select(Invitation).where(
            Invitation.tenant_id == tenant_id,
            Invitation.kind == "staff",
            Invitation.role == "owner",
            Invitation.used_at.is_(None),
        )
    )).scalars().all()
    for s in stale:
        s.expires_at = datetime.now(UTC)

    raw_token, token_verifier = await invite_codes.mint(db)
    inv = Invitation(
        token_verifier=token_verifier,
        tenant_id=tenant_id,
        kind="staff",
        role="owner",
        created_by_user_id=actor_user_id,
        expires_at=datetime.now(UTC) + timedelta(hours=OWNER_INVITE_HOURS),
    )
    db.add(inv)
    await db.flush()
    return _owner_invite_url(raw_token)

# ── tenant create + status change ──────────────────────────────────────────

class TenantIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    slug: str | None = Field(default=None, max_length=64)
    # No literal default. `"lite"` stood here until the catalogue dropped that
    # code in R36, after which every create-shop call that omitted the field
    # stamped a plan no `plans` row matches. Omitted now means "use the platform
    # default", resolved against the live catalogue at request time.
    plan_code: str | None = Field(default=None, max_length=32)
    locale: str = Field(default="uz", pattern="^(uz|ru)$")
    contact_phone: str | None = Field(default=None, max_length=32)
    billing_email: str | None = Field(default=None, max_length=254)
    business_type: str | None = Field(default=None, max_length=48)
    business_type_other: str | None = Field(default=None, max_length=120)

@router.post("/tenants", status_code=status.HTTP_201_CREATED)
async def create_tenant(
    body: TenantIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _require_super(user)
    from app.services.platform_defaults import resolve_plan_code

    plan_code = await resolve_plan_code(db, (body.plan_code or "").strip() or None)
    if plan_code is None:
        # Only reachable when the caller named no plan AND the catalogue is
        # empty. Refusing is the honest answer: the previous behaviour was to
        # stamp the literal `lite`, which is how shops ended up on a plan that
        # does not exist. "Create a plan first" is actionable; a dangling code
        # is not.
        raise HTTPException(status.HTTP_409_CONFLICT, "no_plan_catalogue")
    import re
    base = body.slug or re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-") or "biznes"
    candidate = base
    n = 0
    while (await db.execute(select(Tenant.id).where(Tenant.slug == candidate))).first():
        n += 1
        candidate = f"{base}-{n}"
    tenant = Tenant(
        name=body.name, slug=candidate,
        plan_code=plan_code,
        # Only the built-in "trial" tier enters the demo lifecycle — `expire_demos`
        # later suspends or blocks those. Provisioning on a paid plan must land on
        # `active`, matching the rule `set_plan` already applies when a tenant
        # moves off trial.
        status=TenantStatus.demo if plan_code == "trial" else TenantStatus.active,
        locale=body.locale,
        contact_phone=body.contact_phone,
        billing_email=body.billing_email,
        business_type=body.business_type,
        business_type_other=body.business_type_other,
    )
    db.add(tenant)
    await db.flush()    # need tenant.id for the audit row
    share_url = await _mint_owner_invite(db, tenant_id=tenant.id, actor_user_id=user.id)
    from app.services import audit
    await audit.record(
        db, action="tenant.create", tenant_id=tenant.id,
        actor_user_id=user.id, entity_type="tenant", entity_id=str(tenant.id),
        after={"slug": tenant.slug, "plan_code": tenant.plan_code},
    )
    await db.commit()
    await db.refresh(tenant)
    return {
        "id": str(tenant.id), "slug": tenant.slug, "name": tenant.name,
        "status": tenant.status.value, "share_url": share_url,
    }

@router.post("/tenants/{tenant_id}/owner-invite", status_code=status.HTTP_201_CREATED, dependencies=[invite_limit()])
async def regenerate_owner_invite(
    tenant_id: str,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Mint a fresh single-use owner-claim link for an existing tenant."""
    _require_super(user)
    try:
        tid = UUID(tenant_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_tenant_id") from None
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tid))).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant_not_found")
    share_url = await _mint_owner_invite(db, tenant_id=tenant.id, actor_user_id=user.id)
    await db.commit()
    return {"share_url": share_url, "expires_hours": OWNER_INVITE_HOURS}

#: Money is serialised as a fixed-scale STRING, never a float or a truncated int.
#: The archive is the only surviving record of these numbers, and ``int(...)`` —
#: what the live ``tenant_detail`` drawer does, where it is only ever a display
#: rounding — would silently drop the tiyin the ``Numeric(14,2)`` columns hold.
#: Routed through ``round_amount`` so the archive states amounts under the same
#: ROUND_HALF_UP policy as every other money path (#747).
def _money(v: Decimal | int | None) -> str:
    return str(round_amount(v or 0))


async def _financial_archive(db: AsyncSession, tenant: Tenant) -> dict:
    """(H7) The aggregate financial position of a tenant, captured BEFORE deletion.

    WHY AGGREGATES, AND WHY THESE ONES
    ----------------------------------
    Deleting a tenant is a *destructive* operation on purpose: ``purge_tenant_media``
    below erases KYC and receipt objects, and the FK cascade takes customers,
    contracts and payments with it. So the archive cannot be "keep the rows" — it
    must carry **no personal data at all** while still answering the two questions
    that outlive the shop:

    1. **Accounting** — what was on this shop's books the moment it was removed?
       ``contracts`` (total + per status), ``principal_total``, ``paid_total`` and
       ``outstanding_total``. The three money figures are stored together, not
       derived, so a later reader can cross-check the ledger identity
       ``outstanding = principal - paid`` instead of trusting one number.
    2. **Dispute** — was money in flight when the shop vanished? ``payments`` is
       broken out per status: an *approved* total answers "did it land", a
       *pending* total is precisely what a customer complains about, and a
       *refunded* total is what makes a chargeback argument checkable.

    ``first_contract_at`` / ``last_payment_at`` bound the accounting period the
    figures cover, and ``currencies`` records what they are denominated in — a
    bare number with no currency is not an accounting record. ``balance`` and
    ``topups_paid_total`` capture the *platform↔tenant* side of the money, which
    the tenant-scoped tables do not.

    Counts of customers/staff/products/guarantors are cardinalities only — they
    size the loss without naming anybody.
    """
    tid = tenant.id

    # (MUDD-114) Split by currency. This record exists to survive a dispute, and
    # the docstring above already says a bare number with no currency is not an
    # accounting record — but the totals underneath it were exactly that, summed
    # across every currency the shop ever traded in.
    per_contract_status = (await db.execute(
        select(
            Installment.status,
            Installment.currency,
            func.count(Installment.id),
            func.coalesce(func.sum(Installment.principal), 0),
            func.coalesce(func.sum(Installment.paid_amount), 0),
        )
        .where(Installment.tenant_id == tid)
        .group_by(Installment.status, Installment.currency)
    )).all()
    per_payment_status = (await db.execute(
        select(
            Payment.status,
            Payment.currency,
            func.count(Payment.id),
            func.coalesce(func.sum(Payment.amount), 0),
        )
        .where(Payment.tenant_id == tid)
        .group_by(Payment.status, Payment.currency)
    )).all()

    principal_by_currency = money_by_currency([(r[1], r[3]) for r in per_contract_status])
    paid_by_currency = money_by_currency([(r[1], r[4]) for r in per_contract_status])
    archive_currency = await tenant_primary_currency(db, tid)

    async def _count(model) -> int:
        return (await db.execute(
            select(func.count(model.id)).where(model.tenant_id == tid)
        )).scalar_one()

    first_contract_at, last_contract_at = (await db.execute(
        select(func.min(Installment.created_at), func.max(Installment.created_at))
        .where(Installment.tenant_id == tid)
    )).one()
    last_payment_at = (await db.execute(
        select(func.max(Payment.paid_at)).where(Payment.tenant_id == tid)
    )).scalar_one()
    currencies = sorted((await db.execute(
        select(Installment.currency).where(Installment.tenant_id == tid).distinct()
    )).scalars().all())
    topups_paid = (await db.execute(
        select(func.coalesce(func.sum(TenantTopup.amount), 0)).where(
            TenantTopup.tenant_id == tid, TenantTopup.status == "paid",
        )
    )).scalar_one()

    return {
        "snapshot_at": datetime.now(UTC).isoformat(),
        "currencies": currencies,
        "customers": await _count(Customer),
        "staff": await _count(TenantUser),
        "products": await _count(Product),
        "guarantors": await _count(Guarantor),
        "contracts": {
            "total": sum(r[2] for r in per_contract_status),
            # Each status carries its money PER CURRENCY. A dispute record has to
            # be readable years later by someone who was not here, so every
            # figure names its unit rather than relying on a shop setting that
            # may since have changed.
            "by_status": {
                status_value: {
                    "count": sum(r[2] for r in per_contract_status if r[0].value == status_value),
                    # The scalar keys are UNCHANGED — this blob is a dispute
                    # record and existing readers of it must keep working — but
                    # they are now the `archive_currency` share rather than a
                    # cross-currency sum, with the full split beside them.
                    "principal": _money(primary_total(_st_principal(per_contract_status, status_value), archive_currency)),
                    "paid": _money(primary_total(_st_paid(per_contract_status, status_value), archive_currency)),
                    "principal_by_currency": {
                        c: _money(v) for c, v in _st_principal(per_contract_status, status_value).items()
                    },
                    "paid_by_currency": {
                        c: _money(v) for c, v in _st_paid(per_contract_status, status_value).items()
                    },
                }
                for status_value in {r[0].value for r in per_contract_status}
            },
        },
        # Named outright, because the docstring above is right that a bare number
        # with no currency is not an accounting record — and until now these
        # three were exactly that.
        "archive_currency": archive_currency,
        "principal_total": _money(primary_total(principal_by_currency, archive_currency)),
        "paid_total": _money(primary_total(paid_by_currency, archive_currency)),
        "outstanding_total": _money(
            primary_total(principal_by_currency, archive_currency)
            - primary_total(paid_by_currency, archive_currency)
        ),
        "principal_total_by_currency": {c: _money(v) for c, v in principal_by_currency.items()},
        "paid_total_by_currency": {c: _money(v) for c, v in paid_by_currency.items()},
        "outstanding_total_by_currency": {
            c: _money(v - paid_by_currency.get(c, Decimal(0)))
            for c, v in principal_by_currency.items()
        },
        "payments": {
            "total_count": sum(r[2] for r in per_payment_status),
            "by_status": {
                status_value: {
                    "count": sum(r[2] for r in per_payment_status if r[0].value == status_value),
                    "amount": _money(primary_total(_st_amount(per_payment_status, status_value), archive_currency)),
                    "amount_by_currency": {
                        c: _money(v) for c, v in _st_amount(per_payment_status, status_value).items()
                    },
                }
                for status_value in {r[0].value for r in per_payment_status}
            },
        },
        "first_contract_at": first_contract_at.isoformat() if first_contract_at else None,
        "last_contract_at": last_contract_at.isoformat() if last_contract_at else None,
        "last_payment_at": last_payment_at.isoformat() if last_payment_at else None,
        "balance": _money(tenant.balance),
        "topups_paid_total": _money(topups_paid),
        "balance_txns": (await db.execute(
            select(func.count(TenantBalanceTxn.id)).where(TenantBalanceTxn.tenant_id == tid)
        )).scalar_one(),
    }


@router.delete("/tenants/{tenant_id}", status_code=status.HTTP_200_OK)
async def delete_tenant(
    tenant_id: str,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _require_super(user)
    try:
        tid = UUID(tenant_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_tenant_id") from None
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tid))).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant_not_found")
    slug, name, st = tenant.slug, tenant.name, tenant.status.value
    # (H7) The audit row was IDENTIFICATION only — slug/name/status — so the
    # money left with the cascade. Capture the financial position FIRST: this
    # is the last moment the rows exist, and `audit_log.tenant_id` is
    # ON DELETE SET NULL (models/audit.py), so this row survives the tenant
    # it describes and stays readable by `muddato_platform`.
    archive = await _financial_archive(db, tenant)
    from app.services import audit
    await audit.record(
        db, action="tenant.delete", actor_user_id=user.id,
        entity_type="tenant", entity_id=str(tid),
        before={"slug": slug, "name": name, "status": st, "financial": archive},
    )
    # (#172) Purge the tenant's KYC + receipt OBJECTS from object storage before the FK
    # cascade drops their rows — the cascade frees DB rows but would orphan bucket objects.
    from app.services.privacy import purge_tenant_media
    await purge_tenant_media(db, tid)
    # Drop the ORM object so its (non-cascade) relationships aren't walked at flush.
    db.expunge(tenant)
    # `payments.customer_id` and `installments.customer_id` are ON DELETE RESTRICT,
    # so the tenant→customers cascade would abort while payments/installments still
    # reference those customers. Delete installments first — that DB-cascades their
    # items, guarantor links and payments — then the tenant delete cascades the rest
    # (customers, products, settings, staff, notifications, …).
    await db.execute(sa_delete(Installment).where(Installment.tenant_id == tid))
    await db.execute(sa_delete(Tenant).where(Tenant.id == tid))
    await db.commit()
    return {"deleted": tenant_id, "slug": slug}

# ── tenant detail (professional view) + balance + plan ───────────────────────

async def _apply_balance(db, *, tenant: Tenant, amount: int, kind: str, note: str | None, user_id) -> None:
    """Mutate a tenant's balance and append a ledger row. amount is signed.

    The caller MUST hold a ``FOR UPDATE`` lock on ``tenant`` (load it with
    ``.with_for_update()``): this read-modify-writes the balance, so two concurrent
    callers without the lock would both read the same value and lose an update (#853).
    """
    from decimal import Decimal
    tenant.balance = (tenant.balance or Decimal(0)) + amount
    db.add(TenantBalanceTxn(
        tenant_id=tenant.id, amount=amount, kind=kind, note=note,
        balance_after=tenant.balance, created_by_user_id=user_id,
    ))

def _st_principal(rows: Sequence[Any], status_value: str) -> dict[str, Decimal]:
    """Per-currency principal for one contract status (offboarding archive)."""
    return money_by_currency([(r[1], r[3]) for r in rows if r[0].value == status_value])


def _st_paid(rows: Sequence[Any], status_value: str) -> dict[str, Decimal]:
    """Per-currency paid amount for one contract status."""
    return money_by_currency([(r[1], r[4]) for r in rows if r[0].value == status_value])


def _st_amount(rows: Sequence[Any], status_value: str) -> dict[str, Decimal]:
    """Per-currency amount for one payment status."""
    return money_by_currency([(r[1], r[3]) for r in rows if r[0].value == status_value])


@router.get("/tenants/{tenant_id}/detail")
async def tenant_detail(
    tenant_id: str,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Rich tenant snapshot for the platform drawer — stats + balance + plan."""
    _require_platform(user)
    try:
        tid = UUID(tenant_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_tenant_id") from None
    t = (await db.execute(select(Tenant).where(Tenant.id == tid))).scalar_one_or_none()
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant_not_found")

    customers = (await db.execute(
        select(func.count(Customer.id)).where(Customer.tenant_id == tid)
    )).scalar_one()
    active_contracts = (await db.execute(
        select(func.count(Installment.id)).where(
            Installment.tenant_id == tid,
            Installment.status.in_([InstallmentStatus.active, InstallmentStatus.overdue]),
        )
    )).scalar_one()
    # (MUDD-114) Per currency — a platform admin comparing shops must not be
    # shown one shop's UZS book added to another's USD book.
    financed_by_currency = money_by_currency((await db.execute(
        select(Installment.currency, func.coalesce(func.sum(Installment.principal), 0))
        .where(Installment.tenant_id == tid)
        .group_by(Installment.currency)
    )).all())
    received_by_currency = money_by_currency((await db.execute(
        select(Payment.currency, func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.tenant_id == tid, Payment.status == PaymentStatus.approved,
        ).group_by(Payment.currency)
    )).all())
    tenant_currency = await tenant_primary_currency(db, tid)
    staff = (await db.execute(
        select(func.count(TenantUser.id)).where(TenantUser.tenant_id == tid, TenantUser.is_active.is_(True))
    )).scalar_one()

    return {
        "id": str(t.id), "name": t.name, "slug": t.slug,
        "status": t.status.value, "plan_code": t.plan_code, "locale": t.locale,
        "contact_phone": t.contact_phone, "billing_email": t.billing_email,
        "balance": int(t.balance or 0),
        "plan_overrides": t.plan_overrides or {},
        "created_at": t.created_at.isoformat(),
        "trial_ends_at": t.trial_ends_at.isoformat() if t.trial_ends_at else None,
        "stats": {
            "customers": customers,
            "active_contracts": active_contracts,
            "currency": tenant_currency,
            "financed": int(primary_total(financed_by_currency, tenant_currency)),
            "received": int(primary_total(received_by_currency, tenant_currency)),
            "financed_by_currency": ints_by_currency(financed_by_currency),
            "received_by_currency": ints_by_currency(received_by_currency),
            "staff": staff,
        },
    }

class BalanceTopupIn(BaseModel):
    # (#200) Sane single-credit cap (~$8k) so a fat-finger extra zero is rejected
    # with 422 instead of silently crediting up to a billion so'm.
    amount: int = Field(gt=0, le=100_000_000)   # so'm
    note: str | None = Field(default=None, max_length=300)

@router.post("/tenants/{tenant_id}/balance")
async def topup_balance(
    tenant_id: str,
    body: BalanceTopupIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Add funds to a tenant's prepaid balance (super-admin only)."""
    _require_super(user)
    try:
        tid = UUID(tenant_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_tenant_id") from None
    # (#853) Lock the tenant row before _apply_balance's read+credit, so two concurrent
    # admin credits can't both read the same balance and lose an update.
    t = (await db.execute(
        select(Tenant).where(Tenant.id == tid).with_for_update()
    )).scalar_one_or_none()
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant_not_found")
    await _apply_balance(db, tenant=t, amount=body.amount, kind="topup", note=body.note, user_id=user.id)
    from app.services import audit
    await audit.record(
        db, action="tenant.balance_topup", tenant_id=t.id, actor_user_id=user.id,
        entity_type="tenant", entity_id=str(t.id), after={"amount": body.amount, "balance": int(t.balance)},
    )
    await db.commit()
    return {"balance": int(t.balance)}

# ── tenant-initiated top-ups (review queue) ──────────────────────────────────

@router.get("/topups")
async def list_topups(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: str | None = "pending",
    offset: OffsetQ = 0,
) -> dict:
    """Tenant balance top-up requests for the review queue (pending by default).

    Carries everything the reviewer needs to judge the request without leaving the
    row: the business, the receiving card the money was sent to, the amount, when
    it was submitted, the receipt, the status, and who filed/reviewed it.

    The two actor joins are OUTER: ``created_by_user_id`` / ``reviewed_by_user_id``
    are nullable and are ``SET NULL`` on user delete, so an inner join would drop
    exactly the rows an auditor most needs to see.
    """
    _require_platform(user)
    from app.models import TenantTopup
    creator = aliased(User)
    reviewer = aliased(User)
    q = (
        select(TenantTopup, Tenant.name, creator, reviewer)
        .join(Tenant, Tenant.id == TenantTopup.tenant_id)
        .outerjoin(creator, creator.id == TenantTopup.created_by_user_id)
        .outerjoin(reviewer, reviewer.id == TenantTopup.reviewed_by_user_id)
        .order_by(TenantTopup.created_at.desc(), TenantTopup.id.desc())
    )
    if status_filter:
        q = q.where(TenantTopup.status == status_filter)
    rows = (await db.execute(q.offset(offset).limit(200))).all()
    return {"items": [{
        "id": str(r.id), "tenant_id": str(r.tenant_id), "tenant_name": name,
        "amount": int(r.amount), "method": r.method, "status": r.status,
        "receipt_url": r.receipt_url, "created_at": r.created_at.isoformat(),
        "dest_card_id": r.dest_card_id, "dest_card_label": r.dest_card_label,
        "note": r.note,
        "created_by_name": _display_name(created_by),
        "reviewed_by_name": _display_name(reviewed_by),
        "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
    } for r, name, created_by, reviewed_by in rows]}

@router.post("/topups/{topup_id}/approve")
async def approve_topup(
    topup_id: str,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Confirm a card-transfer top-up and credit the tenant's balance."""
    _require_super(user)
    from app.models import TenantTopup
    from app.services import audit
    from app.services import subscription_checkout as sc
    try:
        tid = UUID(topup_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_id") from None
    topup = (await db.execute(select(TenantTopup).where(TenantTopup.id == tid))).scalar_one_or_none()
    if topup is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "topup_not_found")
    if topup.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "not_pending")
    credited = await sc.mark_topup_paid(db, topup, reviewed_by_user_id=user.id)
    await audit.record(
        db, action="subscription.confirm", tenant_id=topup.tenant_id, actor_user_id=user.id,
        entity_type="topup", entity_id=str(topup.id),
        after={"amount": int(topup.amount), "method": topup.method},
    )
    await db.commit()
    return {"id": str(topup.id), "status": topup.status, "credited": credited}

@router.post("/topups/{topup_id}/reject")
async def reject_topup(
    topup_id: str,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _require_super(user)
    from datetime import datetime as _dt

    from app.models import TenantTopup
    try:
        tid = UUID(topup_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_id") from None
    topup = (await db.execute(select(TenantTopup).where(TenantTopup.id == tid))).scalar_one_or_none()
    if topup is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "topup_not_found")
    if topup.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "not_pending")
    topup.status = "rejected"
    topup.reviewed_by_user_id = user.id
    topup.reviewed_at = _dt.now(UTC)
    await db.commit()
    return {"id": str(topup.id), "status": topup.status}

# Silence unused imports — kept available for future endpoints.
_ = (TenantSubscription, TenantUser)
