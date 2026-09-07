"""Customer-facing endpoints — `/me/*`.

The Mini-App (mobile + browser) at /me/* talks to this router. Auth is
the same JWT as the rest of the app — minted by /auth/telegram/webapp or
/auth/telegram/callback. The user is identified by `current_user`; we
match them to their `Customer` rows via `customer.telegram_id`.

Round 21.1 endpoints (this file)
--------------------------------
* `GET  /me/summary`             — totals across all installments
* `GET  /me/installments`        — list of the user's active+overdue+completed
                                   installments across **all** tenants
* `GET  /me/installments/{id}`   — single installment detail + schedule +
                                   payment history

Round 21.2-21.4 add: /me/payments/initiate, upload-receipt, payoff-quote.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import and_, case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import payment_methods
from app.core.currency import DEFAULT_CURRENCY, money_by_currency
from app.core.dbauth import customer_authority
from app.core.money_utils import round_amount
from app.core.phone_identity import account_phone
from app.core.ratelimit import payment_init_limit
from app.core.security import current_user
from app.db import get_db
from app.models import (
    Customer,
    Installment,
    InstallmentItem,
    Notification,
    Payment,
    Tenant,
    TenantSettings,
    User,
)
from app.models.enums import InstallmentStatus, PaymentStatus
from app.schemas.common import LimitMaxQ, OffsetQ

#: (Batch 5) Every /me route runs under the CUSTOMER authority. What that
#: authority can see is decided in SQL, not here: the customer policies are
#: keyed on `app_verified_telegram_id()`, which is NULL for an account that
#: has not proved it owns an eligible number — so an unproved session reads
#: nothing, and `identity_is_provable` below stays the readable statement of
#: the same rule for the API layer.
router = APIRouter(
    prefix="/me", tags=["customer"],
    dependencies=[Depends(customer_authority)],
)


@router.get("/notifications")
async def my_notifications(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: LimitMaxQ = 30,
    offset: OffsetQ = 0,
) -> dict:
    """The signed-in user's recent in-app notifications (reminders, payment
    approvals, broadcasts) + unread count."""
    rows = (await db.execute(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .offset(offset).limit(limit)
    )).scalars().all()
    unread = (await db.execute(
        select(func.count(Notification.id)).where(
            Notification.user_id == user.id, Notification.read_at.is_(None),
        )
    )).scalar_one()
    return {
        "unread": int(unread or 0),
        "items": [
            {
                "id": str(n.id),
                "kind": n.kind,
                "title": n.title,
                "body": n.body,
                "link": n.link,
                "read": n.read_at is not None,
                "created_at": n.created_at.isoformat(),
            }
            for n in rows
        ],
    }


@router.post("/notifications/read")
async def mark_notifications_read(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Mark all of the user's unread notifications as read."""
    await db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    await db.commit()
    return {"ok": True}


def identity_is_provable(user: User) -> bool:
    """Whether this account has proved it owns an eligible number.

    THE GATE FOR EVERY CUSTOMER READ. A `customers.telegram_id` link is a
    pointer, not an authorisation: links predating phone proof exist, and an
    invitation link can be forwarded. Contracts, schedules and payment history
    are the shop's and the customer's, so reaching them requires the proof, not
    merely a row that mentions this Telegram id.
    """
    from app.core.phone_identity import PhoneEligibility

    return bool(user.verified_phone) and user.phone_eligibility == PhoneEligibility.eligible.value


async def _my_customer_rows(db: AsyncSession, user: User) -> list[Customer]:
    """The Customer rows this person is entitled to see.

    Linked by Telegram id AND gated on proved ownership. One person may be a
    customer of several shops; an unproved account is a customer of none of
    them as far as this API is concerned.
    """
    if user.telegram_id is None or not identity_is_provable(user):
        return []
    return list((await db.execute(
        select(Customer).where(Customer.telegram_id == user.telegram_id)
    )).scalars().all())


def _money(d: Decimal | None) -> str:
    return str(d or Decimal("0"))


# ── the shop's profile, from the one shared resolver ─────────────────────────
# These wrap `services.tenant_profile.resolve` so the customer-facing payloads
# keep their existing key names while the VALUES stop being decided here. Every
# one of them used to pick its own precedence, and two read `tenants.logo_url`,
# a column with no writer anywhere — so a customer never saw their shop's logo.

def _tenant_block(tenant: Tenant, settings: TenantSettings | None) -> dict:
    from app.services.tenant_profile import resolve

    p = resolve(tenant, settings)
    return {
        "id": str(tenant.id), "name": p.name, "logo": p.logo_url, "slug": p.slug,
        "address": p.address, "phone": p.phone, "telegram": p.telegram,
        "hours": p.hours,
    }


def _tenant_name(tenant: Tenant | None, settings: TenantSettings | None) -> str:
    from app.services.tenant_profile import resolve

    return resolve(tenant, settings).name if tenant else ""


def _tenant_logo(tenant: Tenant | None, settings: TenantSettings | None) -> str | None:
    from app.services.tenant_profile import resolve

    return resolve(tenant, settings).logo_url if tenant else None


# ── GET /me/privacy/export · POST /me/privacy/erasure-request (#248) ───────

@router.get("/privacy/export")
async def my_data_export(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """(#248) Right-to-access: the signed-in person's own data across every shop they
    are a customer of. Reuses privacy.export_customer (#167) per record."""
    from app.services.privacy import export_customer
    customers = await _my_customer_rows(db, user)
    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "subject": {
            "telegram_id": user.telegram_id,
            "first_name": user.first_name,
            # The canonical account phone. A data-subject export must describe
            # the identity the system actually holds, and since the typed column
            # stopped being that, exporting it would answer a GDPR access
            # request with a number the account is not known by.
            "phone": account_phone(user),
            # The legacy typed contact detail, if this row still carries one —
            # named for what it is. It is the subject's data and must appear in
            # their export; it is not their identity and must not be labelled as
            # though it were.
            "legacy_contact_phone": user.phone,
        },
        "shops": [await export_customer(db, c) for c in customers],
    }


@router.post("/privacy/erasure-request")
async def my_erasure_request(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """(#248) Right-to-erasure REQUEST. Does NOT erase — erasure is irreversible and the
    shop must keep statutory financial records — so this logs the request + notifies each
    shop's owner(s), who action it via the existing customer-delete flow (which purges
    KYC/receipt media, #172)."""
    from app.models import TenantUser
    from app.models.enums import TenantRole
    from app.services import audit
    customers = await _my_customer_rows(db, user)
    for c in customers:
        await audit.record(
            db, action="customer.erasure_requested", tenant_id=c.tenant_id,
            actor_user_id=user.id, entity_type="customer", entity_id=str(c.id),
            after={"requested_by": "customer_self_service"},
        )
        owner_ids = (await db.execute(
            select(TenantUser.user_id).where(
                TenantUser.tenant_id == c.tenant_id, TenantUser.role == TenantRole.owner,
            )
        )).scalars().all()
        for oid in owner_ids:
            db.add(Notification(
                user_id=oid, kind="erasure_request",
                title="Ma'lumotni o'chirish so'rovi",
                body=f"{c.full_name} o'z shaxsiy ma'lumotlarini o'chirishni so'radi.",
                link=f"/tenant/customers/{c.id}",
            ))
    await db.commit()
    return {"requested": len(customers)}


# ── GET /me/identity ─────────────────────────────────────────────────────

@router.get("/identity")
async def my_identity(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """What the SERVER believes about this person's phone and links.

    The client polls this after asking Telegram to share a contact. It has to:
    the popup's callback reports whether the person tapped "Share", and nothing
    more — the number itself travels to the bot as an authenticated update, on
    its own schedule. A client that concluded "verified" from its own callback
    would be asserting a fact it cannot possibly hold.

    The phone comes back MASKED. It is enough to recognise a number you already
    own and useless to anyone who has taken the session.
    """
    from app.core.phone_identity import PhoneEligibility, mask
    from app.services.display_names import person_name

    verified = bool(user.verified_phone)
    eligibility: str | None = user.phone_eligibility
    if verified and not eligibility:
        # Proved before the eligibility column existed; classify on read rather
        # than reporting a blank the UI would have to guess at.
        from app.core.phone_identity import classify
        eligibility = classify(user.verified_phone)[1].value

    # `verified` implies the block above populated `eligibility`, but that is an
    # invariant of two statements rather than of the column, so the state stays
    # optional here instead of being asserted into a `str`.
    state: str | None
    if not verified:
        state = "VERIFICATION_REQUIRED"
    elif eligibility != PhoneEligibility.eligible.value:
        state = eligibility
    else:
        state = "VERIFIED"

    return {
        "state": state,
        "verified": verified,
        "phoneMasked": mask(user.verified_phone) if verified else None,
        "eligibility": eligibility,
        "source": user.verified_phone_source,
        "verifiedAt": user.verified_phone_at.isoformat() if user.verified_phone_at else None,
        "displayName": person_name(user),
        "linkedShops": len(await _my_customer_rows(db, user)),
    }


# ── GET /me/summary ──────────────────────────────────────────────────────

@router.get("/summary")
async def me_summary(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Total outstanding + this month due + overdue count, aggregated across
    every shop the user has bought from."""
    customers = await _my_customer_rows(db, user)
    if not customers:
        return {
            "currency":            DEFAULT_CURRENCY,
            "total_outstanding":   "0",
            "due_this_month":      "0",
            "due_today":           "0",
            "total_outstanding_by_currency": {},
            "due_today_by_currency":         {},
            "due_this_month_by_currency":    {},
            "overdue_count":       0,
            "installments_count":  0,
            "next_due_date":       None,
            "next_due_amount":     "0",
            "next_due_overdue":    False,
            "next_due_currency":   None,
        }
    customer_ids = [c.id for c in customers]

    today = date.today()
    month_end = today + timedelta(days=30)
    active_states = [
        InstallmentStatus.active, InstallmentStatus.overdue, InstallmentStatus.draft,
    ]
    base = (
        Installment.customer_id.in_(customer_ids),
        Installment.status.in_(active_states),
    )

    # (#78) Aggregate in SQL instead of loading every installment into memory —
    # a customer with thousands of contracts across shops would otherwise OOM.
    # (MUDD-114) GROUPED BY CURRENCY. This aggregate spans every shop the person
    # has bought from, and different shops trade in different currencies — so it
    # was the widest cross-currency sum in the product, adding a USD debt at one
    # shop to a UZS debt at another and calling the result "what you owe".
    #
    # There is no conversion: the customer is told each debt in the unit they
    # agreed to it in. The scalar fields keep their keys but now report the
    # DOMINANT currency (the one they owe most in), named by `currency`, so a
    # client that has not adopted the maps still shows a real figure in a stated
    # unit rather than a sum of unlike things.
    rows = (await db.execute(
        select(
            Installment.currency,
            func.coalesce(func.sum(
                func.coalesce(Installment.principal, 0)
                - func.coalesce(Installment.paid_amount, 0)
            ), 0),
            func.coalesce(func.sum(
                case((Installment.status == InstallmentStatus.overdue, 1), else_=0)
            ), 0),
            func.coalesce(func.sum(
                case((Installment.next_due_date == today, Installment.monthly_amount), else_=0)
            ), 0),
            func.coalesce(func.sum(case(
                (and_(Installment.next_due_date >= today,
                      Installment.next_due_date <= month_end), Installment.monthly_amount),
                else_=0,
            )), 0),
            func.count(Installment.id),
        ).where(*base).group_by(Installment.currency)
    )).all()

    outstanding_by_currency = money_by_currency([(r[0], r[1]) for r in rows])
    due_today_by_currency = money_by_currency([(r[0], r[3]) for r in rows])
    due_month_by_currency = money_by_currency([(r[0], r[4]) for r in rows])
    overdue_count = sum(int(r[2] or 0) for r in rows)
    count_by_currency = {(r[0] or DEFAULT_CURRENCY): int(r[5] or 0) for r in rows}
    installments_count = sum(count_by_currency.values())

    # Dominant = the currency with the most CONTRACTS, not the most money —
    # comparing Decimal amounts across currencies (UZS vs USD) is the exact
    # cross-currency arithmetic MUDD-114 banned everywhere else; a count is
    # unit-free and safe to compare. Deterministic tie-break on the code so
    # two calls with an equal contract count cannot return different headline
    # currencies.
    dominant = (
        max(count_by_currency.items(), key=lambda kv: (kv[1], kv[0]))[0]
        if count_by_currency
        else DEFAULT_CURRENCY
    )
    total_outstanding = outstanding_by_currency.get(dominant)
    due_today = due_today_by_currency.get(dominant)
    due_this_month = due_month_by_currency.get(dominant)

    # (QA H-2) The EARLIEST outstanding due date, whether or not it has passed.
    # This deliberately no longer filters `>= today`: a date in the past matched
    # nothing, so an overdue customer got `next_due_date: null` and the client
    # home read that as "all paid up" — telling the people who are late that
    # they owe nothing, while the shop's aging report counted them as overdue.
    # What a late payer owes is still the next thing they must pay.
    nxt = (await db.execute(
        select(
            Installment.next_due_date,
            Installment.monthly_amount,
            # The contract's own currency — never the shop's current
            # setting, which would relabel an already-agreed debt.
            Installment.currency,
        ).where(
            *base,
            Installment.next_due_date.is_not(None),
        ).order_by(Installment.next_due_date.asc()).limit(1)
    )).first()

    return {
        "currency":           dominant,
        "total_outstanding":  _money(total_outstanding),
        "due_this_month":     _money(due_this_month),
        "due_today":          _money(due_today),
        "total_outstanding_by_currency": {c: _money(v) for c, v in outstanding_by_currency.items()},
        "due_today_by_currency":         {c: _money(v) for c, v in due_today_by_currency.items()},
        "due_this_month_by_currency":    {c: _money(v) for c, v in due_month_by_currency.items()},
        "overdue_count":      int(overdue_count or 0),
        "installments_count": int(installments_count or 0),
        "next_due_date":      nxt[0].isoformat() if nxt and nxt[0] else None,
        "next_due_amount":    _money(nxt[1]) if nxt else "0",
        "next_due_currency":  nxt[2] if nxt else None,
        # Stated outright rather than left to the client to re-derive from the
        # date — that inference is exactly what was missing, and a client that
        # forgets it shows a late customer a cheerful "nothing due".
        "next_due_overdue":   bool(nxt and nxt[0] and nxt[0] < today),
    }


# ── GET /me/installments ─────────────────────────────────────────────────

@router.get("/installments")
async def list_my_installments(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: str | None = None,  # "open" (default) | "completed" | "all"
    limit: LimitMaxQ = 20,
    offset: OffsetQ = 0,
) -> list[dict]:
    """List the user's installments + the shop name + the most-relevant
    next-step. Bounded + batched (#78): page size is capped and the per-row
    tenant/item lookups collapse into two IN queries (was 1+2N)."""
    customers = await _my_customer_rows(db, user)
    if not customers:
        return []
    customer_ids = [c.id for c in customers]

    q = select(Installment).where(Installment.customer_id.in_(customer_ids))
    sf = (status_filter or "open").lower()
    if sf == "open":
        q = q.where(Installment.status.in_([
            InstallmentStatus.active, InstallmentStatus.overdue,
        ]))
    elif sf == "completed":
        q = q.where(Installment.status == InstallmentStatus.completed)
    # else "all" — no filter

    rows = (await db.execute(
        q.order_by(Installment.next_due_date.asc().nulls_last(), Installment.id.asc())
        .offset(offset).limit(limit)
    )).scalars().all()
    if not rows:
        return []

    # (#78) Batch the per-row lookups (was 1+2N): one query for tenants, one for items.
    tenant_ids = {inst.tenant_id for inst in rows}
    tenants = {
        t.id: t for t in (await db.execute(
            select(Tenant).where(Tenant.id.in_(tenant_ids))
        )).scalars()
    }
    # Batched with the tenants, for the same reason they are (#78): the shop
    # profile now resolves from both tables, and doing that per row would
    # reintroduce the N+1 this block exists to avoid.
    settings_by_tenant = {
        s.tenant_id: s for s in (await db.execute(
            select(TenantSettings).where(TenantSettings.tenant_id.in_(tenant_ids))
        )).scalars()
    }
    items_by_inst: dict[UUID, list[InstallmentItem]] = {}
    for it in (await db.execute(
        select(InstallmentItem)
        .where(InstallmentItem.installment_id.in_([inst.id for inst in rows]))
        .order_by(InstallmentItem.installment_id)
    )).scalars():
        items_by_inst.setdefault(it.installment_id, []).append(it)

    out: list[dict] = []
    today = date.today()
    for inst in rows:
        tenant = tenants.get(inst.tenant_id)
        items = items_by_inst.get(inst.id, [])[:3]
        if len(items) == 0:
            item_summary = "—"
        elif len(items) == 1:
            it = items[0]
            item_summary = f"{it.product_name_snapshot}{' · ' + it.variant_label if it.variant_label else ''}"
        else:
            item_summary = f"{items[0].product_name_snapshot} +{len(items) - 1}"

        # Compute next-step nuance for the UI's "headline" row.
        next_due = inst.next_due_date
        if next_due is None:
            headline = None
        elif next_due < today:
            headline = {"kind": "overdue", "days": (today - next_due).days,
                        "amount": _money(inst.monthly_amount)}
        elif next_due == today:
            headline = {"kind": "due_today", "amount": _money(inst.monthly_amount)}
        else:
            headline = {"kind": "upcoming",
                        "days": (next_due - today).days,
                        "due_date": next_due.isoformat(),
                        "amount": _money(inst.monthly_amount)}

        out.append({
            "id":              str(inst.id),
            "tenant_id":       str(inst.tenant_id),
            # Same resolver as every other surface — this list showed the
            # provisioning name and a permanently-null logo.
            "tenant_name":     _tenant_name(tenant, settings_by_tenant.get(inst.tenant_id)),
            "tenant_logo":     _tenant_logo(tenant, settings_by_tenant.get(inst.tenant_id)),
            "currency":        inst.currency,
            "contract_number": inst.contract_number,
            "reference":       inst.reference,
            "item_summary":    item_summary,
            "principal":       _money(inst.principal),
            "paid_amount":     _money(inst.paid_amount),
            "remaining":       _money(inst.remaining_balance),
            "monthly_amount":  _money(inst.monthly_amount),
            "months":          inst.months,
            "paid_count":      inst.paid_count,
            "status":          inst.status.value,
            "next_due_date":   next_due.isoformat() if next_due else None,
            "headline":        headline,
            "start_date":      inst.start_date.isoformat() if inst.start_date else None,
        })
    return out


# ── GET /me/installments/{id} ────────────────────────────────────────────

@router.get("/installments/{installment_id}")
async def my_installment_detail(
    installment_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Detail view + synthesised payment schedule + payment history."""
    customers = await _my_customer_rows(db, user)
    customer_ids = {c.id for c in customers}

    inst = (await db.execute(
        select(Installment).where(Installment.id == installment_id)
    )).scalar_one_or_none()
    if inst is None or inst.customer_id not in customer_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "installment_not_found")

    tenant = (await db.execute(select(Tenant).where(Tenant.id == inst.tenant_id))).scalar_one()
    settings = (await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == inst.tenant_id)
    )).scalar_one_or_none()
    items = (await db.execute(
        select(InstallmentItem).where(InstallmentItem.installment_id == inst.id)
    )).scalars().all()
    payments = (await db.execute(
        select(Payment)
        .where(Payment.installment_id == inst.id)
        .order_by(Payment.created_at.desc())
    )).scalars().all()

    # Build a 12-row schedule projection.
    # Mark each row's status: "paid" (covered by prior payments) / "next" /
    # "future". We use paid_count as the simple watermark.
    schedule: list[dict] = []
    cur = inst.start_date
    for i in range(1, (inst.months or 0) + 1):
        cur = cur + timedelta(days=30) if cur else cur
        if i <= (inst.paid_count or 0):
            row_status = "paid"
        elif i == (inst.paid_count or 0) + 1:
            row_status = "next"
        else:
            row_status = "future"
        schedule.append({
            "n":        i,
            "due_date": cur.isoformat() if cur else None,
            "amount":   _money(inst.monthly_amount),
            "status":   row_status,
        })

    today = date.today()
    next_due = inst.next_due_date
    headline = None
    if next_due is not None:
        if next_due < today:
            headline = {"kind": "overdue", "days": (today - next_due).days}
        elif next_due == today:
            headline = {"kind": "due_today"}
        else:
            headline = {"kind": "upcoming", "days": (next_due - today).days}

    return {
        "id":              str(inst.id),
        # One resolution of the shop's profile (services/tenant_profile). This
        # block mixed three sources with three different rules: the name came
        # from the provisioning row rather than the owner's own, and the logo
        # came from `tenants.logo_url`, a column nothing has been able to write
        # since the business profile moved — so it was always null here while
        # the same shop's contracts rendered a logo.
        "tenant": _tenant_block(tenant, settings),
        "contract_number": inst.contract_number,
        # The contract's own currency. A missing one is a data
        # integrity error after migration ra0currency, not a reason to guess.
        "currency":        inst.currency,
        "reference":       inst.reference,
        "status":          inst.status.value,
        "principal":       _money(inst.principal),
        "paid_amount":     _money(inst.paid_amount),
        "remaining":       _money(inst.remaining_balance),
        "monthly_amount":  _money(inst.monthly_amount),
        "total_amount":    _money(inst.total_amount),
        "down_payment":    _money(inst.down_payment),
        "months":          inst.months,
        "paid_count":      inst.paid_count,
        "start_date":      inst.start_date.isoformat() if inst.start_date else None,
        "next_due_date":   next_due.isoformat() if next_due else None,
        "headline":        headline,
        "items": [
            {
                "name":       it.product_name_snapshot,
                "variant":    it.variant_label,
                "serial":     it.serial_number,
                "qty":        it.qty,
                "unit_price": _money(it.unit_price),
                "line_total": _money(it.line_total),
            }
            for it in items
        ],
        "schedule": schedule,
        "payments": [
            {
                "id":          str(p.id),
                "amount":      _money(p.amount),
                "currency":    p.currency,        # "method":      p.method.value,
                "status":      p.status.value,
                "paid_at":     p.paid_at.isoformat() if p.paid_at else None,
                "external_ref": p.external_ref,
                "receipt_image_url": p.receipt_image_url,
            }
            for p in payments
            if p.status in (PaymentStatus.approved, PaymentStatus.pending)
            and p.method != PaymentMethod.late_fee  # (#121) accrual, not a payment the customer made
        ],
    }


# ╭─────────────────────────────────────────────────────────────────────╮
# │ Round 21.2 — payment initiation + status polling                    │
# ╰─────────────────────────────────────────────────────────────────────╯

from urllib.parse import urlencode

from pydantic import BaseModel, Field

from app.config import settings as app_settings
from app.models.enums import PaymentMethod

#: What `initiate` accepts: every method the product currently OFFERS, plus the
#: online providers whose flows are still wired (they answer with an
#: explanatory 409 when a shop has not configured them).
#:
#: (QA M-3) This was the hand-written literal `^(click|payme|cash)$`, which had
#: drifted into the exact inverse of the product: it admitted both WITHDRAWN
#: providers and refused `card`, one of the two methods actually offered. The
#: app listed "Karta" and then rejected it as invalid input. Deriving the set
#: from `payment_methods` means withdrawing or restoring a method updates this
#: endpoint by itself.
_INITIATE_METHODS: tuple[str, ...] = tuple(
    dict.fromkeys((*payment_methods.OFFERED, "click", "payme"))
)


class InitiatePaymentIn(BaseModel):
    installment_id: UUID
    amount: Decimal = Field(gt=0, decimal_places=2, max_digits=14)
    method: str = Field(pattern=rf"^({'|'.join(_INITIATE_METHODS)})$")


class InitiatePaymentOut(BaseModel):
    payment_id: str
    method: str
    pay_url: str | None = None
    deep_link: str | None = None
    expires_in_seconds: int = 600


def _build_click_url(*, service_id: str, merchant_id: str, amount: Decimal,
                     payment_id: str, return_url: str) -> str:
    qs = urlencode({
        "service_id":         service_id,
        "merchant_id":        merchant_id,
        "amount":             str(round_amount(amount)),  # (#747) ROUND_HALF_UP
        "transaction_param":  payment_id,
        "return_url":         return_url,
    })
    return f"https://my.click.uz/services/pay?{qs}"


def _build_payme_url(*, merchant_id: str, amount: Decimal, installment_id: str,
                     return_url: str) -> str:
    """Payme checkout deep-link.

    Payme expects a base64'd `;`-joined param string. We build the canonical
    "ac.installment_id=...;a=<tiyin>;m=<merchant>;c=<return>" payload and
    base64-encode it, then prefix `https://checkout.paycom.uz/`.
    """
    import base64
    amount_tiyin = int((amount * Decimal(100)).to_integral_value())
    payload = (
        f"m={merchant_id};"
        f"ac.installment_id={installment_id};"
        f"a={amount_tiyin};"
        f"c={return_url}"
    )
    return "https://checkout.paycom.uz/" + base64.b64encode(payload.encode()).decode()


@router.post("/payments/initiate", response_model=InitiatePaymentOut, dependencies=[payment_init_limit()])
async def initiate_payment(
    body: InitiatePaymentIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> InitiatePaymentOut:
    """Mint a pending Payment + return the right deep-link for the chosen
    method. Customer's wallet app (Click/Payme) then drives the flow until
    the provider webhook flips the Payment to approved.

    For `cash`, we don't create a Payment row yet — Round 21.3 receipt
    upload is the next step. Returns an empty deep-link so the frontend
    forwards to the upload flow.
    """
    customers = await _my_customer_rows(db, user)
    customer_ids = {c.id for c in customers}

    inst = (await db.execute(
        select(Installment).where(Installment.id == body.installment_id)
    )).scalar_one_or_none()
    if inst is None or inst.customer_id not in customer_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "installment_not_found")
    if inst.status in (InstallmentStatus.completed, InstallmentStatus.cancelled):
        raise HTTPException(status.HTTP_409_CONFLICT, "installment_closed")
    # Don't let a customer charge more than they owe.
    remaining = inst.remaining_balance
    if body.amount > remaining:
        raise HTTPException(status.HTTP_409_CONFLICT, "amount_exceeds_remaining")

    # For online providers we need the tenant's merchant creds.
    tsettings = (await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == inst.tenant_id)
    )).scalar_one_or_none()

    if body.method in ("cash", "card"):
        # Both are OFFLINE: the customer pays in person or transfers to the
        # shop's card, then uploads a receipt. No row yet — the receipt-upload
        # endpoint creates the pending row with the file attached, and the
        # frontend forwards to the upload UI.
        if body.method == "card" and not payment_methods.card_is_usable(
            tsettings.payment_methods if tsettings else None
        ):
            # Mirrors click/payme: name the reason rather than failing as
            # unparseable input, so the shop knows what to configure.
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "card_not_configured — do'kon qabul qiluvchi kartani sozlamagan",
            )
        return InitiatePaymentOut(
            payment_id="",
            method=body.method,
            pay_url=None,
            deep_link=None,
        )

    customer = next(iter(c for c in customers if c.id == inst.customer_id))
    payment = Payment(
        tenant_id=inst.tenant_id,
        installment_id=inst.id,
        customer_id=customer.id,
        currency=inst.currency,          # from the contract, never the shop
        amount=body.amount,
        method=PaymentMethod(body.method),
        status=PaymentStatus.pending,
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    return_url = f"{app_settings.frontend_url.rstrip('/')}/uz/me/installments/{inst.id}"
    if body.method == "click":
        if not (tsettings and tsettings.click_service_id and tsettings.click_merchant_id):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "click_not_configured — do'kon Click hisobini sozlamagan",
            )
        pay_url = _build_click_url(
            service_id=tsettings.click_service_id,
            merchant_id=tsettings.click_merchant_id,
            amount=body.amount,
            payment_id=str(payment.id),
            return_url=return_url,
        )
        return InitiatePaymentOut(payment_id=str(payment.id), method="click", pay_url=pay_url, deep_link=pay_url)
    if body.method == "payme":
        if not (tsettings and tsettings.payme_merchant_id):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "payme_not_configured — do'kon Payme hisobini sozlamagan",
            )
        pay_url = _build_payme_url(
            merchant_id=tsettings.payme_merchant_id,
            amount=body.amount,
            installment_id=str(inst.id),
            return_url=return_url,
        )
        return InitiatePaymentOut(payment_id=str(payment.id), method="payme", pay_url=pay_url, deep_link=pay_url)

    raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown_method")


@router.get("/payments/{payment_id}/status")
async def payment_status(
    payment_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Polling endpoint — the Mini-App calls this every few seconds after
    forwarding the user to the provider. Returns 404 if the user isn't
    the customer on the underlying installment."""
    customers = await _my_customer_rows(db, user)
    customer_ids = {c.id for c in customers}

    pay = (await db.execute(select(Payment).where(Payment.id == payment_id))).scalar_one_or_none()
    if pay is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "payment_not_found")
    if pay.customer_id not in customer_ids:
        # (#89) Uniform 404 (as the docstring says) — don't reveal to a non-owner
        # that the payment exists.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "payment_not_found")
    return {
        "id":          str(pay.id),
        "status":      pay.status.value,
        "method":      pay.method.value,
        "amount":      _money(pay.amount),
        "currency":    pay.currency,              # "paid_at":     pay.paid_at.isoformat() if pay.paid_at else None,
        "external_ref": pay.external_ref,
    }


@router.get("/installments/{installment_id}/payment-methods")
async def list_payment_methods(
    installment_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Which methods are available for this customer + installment?
    Driven by tenant_settings.payment_methods + the presence of credentials
    for online providers."""
    customers = await _my_customer_rows(db, user)
    customer_ids = {c.id for c in customers}
    inst = (await db.execute(select(Installment).where(Installment.id == installment_id))).scalar_one_or_none()
    if inst is None or inst.customer_id not in customer_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "installment_not_found")
    s = (await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == inst.tenant_id)
    )).scalar_one_or_none()
    enabled = (s.payment_methods if s else {}) or {}
    methods = []
    # (#14) Every branch is gated on the shared offered-set rather than being
    # deleted outright: a tenant who had Click configured keeps that config in
    # the database untouched, and restoring the method is a one-word change in
    # app/core/payment_methods.py — not an archaeology exercise here.
    if payment_methods.is_offered("click") and enabled.get("click") and s and s.click_service_id and s.click_merchant_id:
        methods.append({"method": "click", "label": "Click", "online": True})
    if payment_methods.is_offered("payme") and enabled.get("payme") and s and s.payme_merchant_id:
        methods.append({"method": "payme", "label": "Payme", "online": True})
    # cash is always offered — Round 21.3 receipt upload covers it
    if payment_methods.is_offered("cash") and enabled.get("cash", True):
        methods.append({"method": "cash", "label": "Naqd", "online": False})
    # Card is offered only once the tenant has somewhere to receive the money;
    # otherwise the customer would pick it and find an empty card list.
    if payment_methods.is_offered("card") and payment_methods.card_is_usable(enabled):
        methods.append({"method": "card", "label": "Karta", "online": False})
    return {"methods": methods, "card_instructions": s.card_payment_instructions if s else None}


# ╭─────────────────────────────────────────────────────────────────────╮
# │ Round 21.3 — receipt photo upload (cash flow)                       │
# ╰─────────────────────────────────────────────────────────────────────╯

from fastapi import File, Form, UploadFile

# (#821) Size is capped by the single app-wide MAX_UPLOAD_BYTES (8 MB), matched by
# media.py and the frontend (lib/image IMAGE_MAX_BYTES) — no per-endpoint drift.
# The allowlist stops accidental video/other uploads; a receipt photo rarely >2 MB.
RECEIPT_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/heic", "image/webp", "application/pdf"}


@router.post("/payments/upload-receipt", dependencies=[payment_init_limit()])
async def upload_receipt(
    background: BackgroundTasks,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    installment_id: Annotated[UUID, Form()],
    amount: Annotated[Decimal, Form()],
    file: Annotated[UploadFile | None, File()] = None,
    method: Annotated[str, Form()] = PaymentMethod.cash.value,
) -> dict:
    """Manual-rail flow: the customer tells the shop what they paid. We:

    1. Validate access + content type + size
    2. Persist the file via Supabase Storage if configured (env vars
       SUPABASE_* set), else fall back to a data: URL embedded in
       receipt_image_url. The latter is fine for MVP since photos are
       ~few hundred KB and the table is per-tenant scoped.
    3. Create a Payment(pending) row referencing the URL
    4. Notify the tenant owner via Telegram so they can approve

    Tenant approves via the existing `/tenant/payments/{id}/approve`
    endpoint (Round 17.4 — already wired).

    THE PROOF IS REQUIRED FOR EVERY METHOD EXCEPT CASH, and that distinction is
    the point of this endpoint taking a `method` at all.

    A card or transfer happened somewhere we cannot see, so the image is the
    only evidence the reviewer has that it happened at all — required, as it
    always was.

    Cash was handed over the counter. There is frequently no chek to photograph,
    and the shop that is being asked to approve the payment is the shop that
    took the money — so demanding a photo asked the customer to produce evidence
    for something the approver already witnessed, and blocked the notification
    when they could not. The payment is still created `pending` and still
    approved by the tenant, so nothing is credited on the customer's say-so;
    only the impossible attachment is gone.

    `method` defaults to cash so an older client that posts without it behaves
    exactly as before.
    """
    from app.core import payment_methods
    from app.services.notifications import miniapp_deeplink, notify_payment_review_bg

    # Blank is treated as unset, i.e. cash. FastAPI already applies the default
    # when the field is absent, and a client that sends an empty one means the
    # same thing. This is the permissive direction — cash is the rail that needs
    # no proof — and it is safe here only because the payment still lands
    # `pending` for the tenant to approve, and because a customer could simply
    # choose cash in the UI anyway. The approval gate is the control; the method
    # is a label on a request the shop still has to accept.
    chosen = (method or "").strip().lower() or PaymentMethod.cash.value
    if not payment_methods.is_offered(chosen):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "method_not_offered")
    requires_proof = chosen != PaymentMethod.cash.value

    if file is None and requires_proof:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "receipt_required")
    if file is not None and file.content_type not in RECEIPT_ALLOWED_TYPES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"unsupported_type: {file.content_type}",
        )

    customers = await _my_customer_rows(db, user)
    customer_ids = {c.id for c in customers}
    inst = (await db.execute(
        select(Installment).where(Installment.id == installment_id)
    )).scalar_one_or_none()
    if inst is None or inst.customer_id not in customer_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "installment_not_found")
    if amount <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "amount_invalid")
    remaining = inst.remaining_balance
    if amount > remaining:
        raise HTTPException(status.HTTP_409_CONFLICT, "amount_exceeds_remaining")

    # (#821) Capped read via the shared helper + the single app-wide upload cap
    # (MAX_UPLOAD_BYTES, 8 MB — matched by media.py and the frontend). Streams in
    # chunks and raises 413 the moment the cap is exceeded.
    receipt_url: str | None = None
    if file is not None:
        from app.core.limits import MAX_UPLOAD_BYTES, read_capped
        content = await read_capped(file, MAX_UPLOAD_BYTES)

        # Persist the bytes in receipt_blobs and get back a short signed URL.
        receipt_url = await _store_receipt(
            db, content, file.content_type or "image/jpeg", tenant_id=inst.tenant_id,
            uploaded_by=user.id,
            # (#817) Idempotent on retry: the same installment + amount + photo derives
            # the same key, so a double-submit reuses the row instead of re-sending.
            idempotency_discriminator=f"{installment_id}|{amount}",
        )

    customer = next(c for c in customers if c.id == inst.customer_id)
    payment = Payment(
        tenant_id=inst.tenant_id,
        installment_id=inst.id,
        customer_id=customer.id,
        currency=inst.currency,          # from the contract, never the shop
        amount=amount,
        # Recorded as CHOSEN, not as cash-for-everything. Every manual payment
        # used to be stored as cash regardless of what the customer picked,
        # which put card money in the cash column of the shop's own reporting.
        method=PaymentMethod(chosen),
        status=PaymentStatus.pending,
        receipt_image_url=receipt_url,
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    # Notify the tenant owner in the BACKGROUND — the customer gets an instant
    # response instead of waiting on the owner's Telegram round-trip(s).
    #
    # The button REPLACES the raw URL that used to sit in the message body. That
    # link went to the generic installments list, so the reviewer arrived and
    # had to hunt for the receipt that had just been mentioned to them. It now
    # opens the exact pending payment.
    #
    # Tapping it does NOT approve anything. It opens the review screen; approve
    # and reject are separate authenticated actions taken after the reviewer has
    # actually looked at the receipt. A one-tap approve from a chat notification
    # would be a money decision made without seeing the evidence.
    from app.core.documents import money_with_currency
    from app.core.review_intent import PURPOSE_PAYMENT_REVIEW, mint
    from app.services.document_delivery import safe_display_name
    from app.services.notifications import MiniAppButton

    intent = mint(PURPOSE_PAYMENT_REVIEW, payment.id)
    base = app_settings.frontend_url.rstrip("/")
    background.add_task(
        notify_payment_review_bg,
        tenant_id=inst.tenant_id,
        payment_id=payment.id,
        title="📸 Yangi chek" if receipt_url else "💵 Naqd to'lov",
        body=(
            f"{safe_display_name(customer.full_name)} "
            f"{money_with_currency(amount, inst.currency, 'uz')} "
            # Saying "sent a receipt" when no receipt exists sends the reviewer
            # looking for an attachment that is not there.
            f"{'chek yubordi' if receipt_url else 'naqd to‘ladi deb bildirdi'}.\n"
            f"Shartnoma: {inst.contract_number or str(inst.id)[:8].upper()}"
        ),
        mini_app=MiniAppButton(
            label="🔍 Chekni tekshirish",
            # Private chats: opens inside Telegram with signed initData, so the
            # screen authenticates the person who tapped.
            mini_app_url=f"{base}/uz/tenant/payments/{payment.id}/review",
            # Groups and topics, where web_app buttons are not available.
            deep_link=miniapp_deeplink(intent),
        ),
    )

    return {
        "payment_id":         str(payment.id),
        "status":             payment.status.value,
        "receipt_image_url":  receipt_url,
    }


async def _store_receipt(
    db: AsyncSession, content: bytes, content_type: str, *, tenant_id: UUID,
    uploaded_by: UUID | None = None, idempotency_discriminator: str | None = None,
) -> str:
    """Persist a customer cash-receipt and return its signed capability URL. The
    routing (Telegram-group media when enabled, else the legacy private receipt
    store) lives in the shared payment-proof gate so the customer-receipt and
    tenant-top-up paths can never diverge (#820). ``idempotency_discriminator``
    makes a retried upload idempotent on the Telegram path (#817)."""
    from app.services.media import store_payment_proof

    return await store_payment_proof(
        db, content, content_type, tenant_id=tenant_id,
        entity_type="payment_receipt", uploaded_by=uploaded_by,
        idempotency_discriminator=idempotency_discriminator,
    )


# ╭─────────────────────────────────────────────────────────────────────╮
# │ Round 21.4 — early payoff quote + execute                            │
# ╰─────────────────────────────────────────────────────────────────────╯


@router.get("/installments/{installment_id}/payoff-quote")
async def payoff_quote(
    installment_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Compute how much the customer would pay to close this installment
    today, including any early-payoff discount the tenant has configured.

    Quote elements:
      remaining_principal      — what's owed
      discount_pct             — tenant's `early_payoff_discount_pct`
      discount_amount          — flat amount knocked off
      payoff_amount            — what customer actually pays
      savings_vs_schedule      — sum of remaining scheduled monthly
                                 amounts minus payoff_amount; helps the
                                 UI render "Siz X so'm tejaysiz"
    """
    customers = await _my_customer_rows(db, user)
    customer_ids = {c.id for c in customers}
    inst = (await db.execute(
        select(Installment).where(Installment.id == installment_id)
    )).scalar_one_or_none()
    if inst is None or inst.customer_id not in customer_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "installment_not_found")
    if inst.status in (InstallmentStatus.completed, InstallmentStatus.cancelled):
        raise HTTPException(status.HTTP_409_CONFLICT, "installment_closed")

    ts = (await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == inst.tenant_id)
    )).scalar_one_or_none()
    discount_pct = (
        ts.early_payoff_discount_pct if ts and getattr(ts, "early_payoff_discount_pct", None) is not None
        else Decimal("0")
    )

    remaining_principal = inst.remaining_balance
    discount_amount = round_amount(remaining_principal * discount_pct / Decimal(100))  # (#747)
    payoff_amount = round_amount(remaining_principal - discount_amount)

    months_left = max(0, (inst.months or 0) - (inst.paid_count or 0))
    scheduled_total = round_amount((inst.monthly_amount or Decimal("0")) * Decimal(months_left))
    savings = round_amount(scheduled_total - payoff_amount)

    return {
        "installment_id":     str(inst.id),
        "remaining_principal": _money(remaining_principal),
        "discount_pct":        str(discount_pct),
        "discount_amount":     _money(discount_amount),
        "payoff_amount":       _money(payoff_amount),
        "scheduled_total":     _money(scheduled_total),
        "savings_vs_schedule": _money(savings if savings > 0 else Decimal("0")),
        "months_left":         months_left,
    }


class PayoffExecuteIn(BaseModel):
    method: str = Field(pattern=r"^(click|payme|cash)$")


@router.post("/installments/{installment_id}/payoff", response_model=InitiatePaymentOut, dependencies=[payment_init_limit()])
async def execute_payoff(
    installment_id: UUID,
    body: PayoffExecuteIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> InitiatePaymentOut:
    """Initiate a payment for the *exact* payoff_amount; on settlement the
    installment closes.

    (#108) Before initiating, persist the early-payoff discount on the
    installment. Otherwise the customer pays ``principal - discount`` and
    ``recompute_state`` — which closes a loan at ``paid >= principal`` — never
    flips it to completed, leaving a residual balance equal to the discount and
    resuming overdue reminders/late fees on a loan they were told they paid off.
    Recording ``payoff_discount_amount`` lowers the settlement threshold so the
    webhook that applies this payment closes the loan.
    """
    quote = await payoff_quote(installment_id, user, db)
    discount = Decimal(quote["discount_amount"])
    if discount > 0:
        # (MUDD-63/H4) Lock the row before writing payoff_discount_amount — the
        # payment webhooks (payme.py/click.py) lock this same row via
        # apply_payment() and clear the discount when a non-settling payment
        # lands. Only this handler ever locks Installment without first
        # locking a Payment row, so this can't invert the Payment→Installment
        # order those webhooks use and cannot deadlock against them.
        inst = (await db.execute(
            select(Installment).where(Installment.id == installment_id)
            .with_for_update()
        )).scalar_one_or_none()
        # Re-check status under the lock: the quote above was read before the
        # lock was granted, so a payment can land (and settle or cancel this
        # contract) while this call waits for it. Writing a stale discount
        # onto an already-closed loan would leave it sitting there for a
        # later reversal/reopen to pick back up.
        if inst is not None and inst.status not in (
            InstallmentStatus.completed, InstallmentStatus.cancelled,
        ):
            inst.payoff_discount_amount = discount
            await db.commit()
    return await initiate_payment(
        InitiatePaymentIn(
            installment_id=installment_id,
            amount=Decimal(quote["payoff_amount"]),
            method=body.method,
        ),
        user, db,
    )
