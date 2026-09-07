"""Subscription billing — invoice math, generation, and dunning (Round 27).

Pricing model
-------------
* **Fixed plans** (Lite / Pro) bill a flat ``Plan.price_monthly`` per period,
  regardless of usage.
* **Custom plan** bills per active installment:
  ``count(active|overdue Installments) × Plan.price_per_installment``.
  This lets a high-volume shop pay only for what it carries.

Dunning ladder (driven by ``Invoice.due_at`` + status ``overdue``)
------------------------------------------------------------------
* unpaid past ``due_at``         → invoice flips ``open`` → ``overdue``
* overdue + 3 days               → tenant ``active`` → ``suspended``
* overdue + 14 days              → tenant ``suspended`` → ``blocked``

The cron (``app/services/scheduler.py``) calls :func:`run_billing_cycle` and
:func:`run_dunning` daily. All money is ``Decimal``; never float.

[LEGAL_REVIEW] Invoice wording, VAT/QQS handling, and the grace-period
lengths need confirmation with a UZ accountant before public launch.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business_time import tashkent_today
from app.core.money_utils import round_amount
from app.models import Installment, Invoice, Plan, Tenant, TenantBalanceTxn
from app.models.enums import (
    InstallmentStatus,
    InvoiceStatus,
    TenantStatus,
)

log = logging.getLogger("muddato.billing")

# Dunning offsets (days after invoice due date).
SUSPEND_AFTER_DUE_DAYS = 3
BLOCK_AFTER_DUE_DAYS = 14
# Net payment term: invoice issued today is due in this many days.
DUE_NET_DAYS = 7
# Pro→Custom upsell only fires above this many active credits — below it the
# flat Pro price is almost always the cheaper option.
SUGGEST_CUSTOM_MIN_ACTIVE = 150

# (F-19) Dunning pages the per-tenant ladder in keyset batches of this size
# (ordered by tenant_id) so memory stays bounded regardless of how many tenants
# carry overdue invoices. Mirrors scheduler._CRON_BATCH.
_DUNNING_BATCH = 500

_ACTIVE_STATES = (InstallmentStatus.active, InstallmentStatus.overdue)


# ── usage ──────────────────────────────────────────────────────────────────

async def active_installment_count(db: AsyncSession, tenant_id: UUID) -> int:
    """Count installments that are live (active or overdue) for a tenant."""
    return int((await db.execute(
        select(func.count(Installment.id)).where(
            Installment.tenant_id == tenant_id,
            Installment.status.in_(_ACTIVE_STATES),
        )
    )).scalar_one())


# ── amount math ──────────────────────────────────────────────────────────────

def compute_amount(
    plan: Plan, active_count: int, *,
    price_monthly: int | None = None,
    contract_overage: dict | None = None,
    included_contracts: int | None = "__plan__",  # type: ignore[assignment]
) -> tuple[Decimal, dict]:
    """Subscription + overage math for one period.

    ``amount = base monthly + (active contracts above the included quota) ×  # noqa: RUF002
    per-contract overage``. Flex (included = 0) bills every active contract;
    unlimited plans (included = None) never add contract overage. No plan-name
    hardcoding — behaviour comes from the limit + overage config. Per-tenant
    overrides may pass ``price_monthly`` / ``contract_overage`` / ``included``.
    """
    base = Decimal(price_monthly if price_monthly is not None else (plan.price_monthly or 0))
    lines = [{
        "label": f"{plan.name_uz} — oylik obuna",
        "qty": 1, "unit_price": str(base), "total": str(base),
    }]
    total = base

    included = plan.max_active_installments if included_contracts == "__plan__" else included_contracts
    ov = contract_overage if contract_overage is not None else (plan.overage or {}).get("contract")
    ov = ov or {}
    if ov.get("enabled") and str(ov.get("mode")) == "soft" and included is not None:
        extra = max(0, active_count - int(included))
        if extra:
            unit = Decimal(ov.get("price") or 0)
            amt = round_amount(unit * extra)  # (#747) ROUND_HALF_UP
            lines.append({
                "label": "Qo'shimcha faol shartnomalar",
                "qty": extra, "unit_price": str(unit), "total": str(amt),
            })
            total += amt

    return round_amount(total), {"lines": lines, "model": "subscription_overage"}


async def _plan_for(db: AsyncSession, code: str) -> Plan | None:
    return (await db.execute(
        select(Plan).where(Plan.code == code, Plan.is_active.is_(True))
    )).scalar_one_or_none()


# ── invoice generation ───────────────────────────────────────────────────────

def _period_bounds(today: date) -> tuple[date, date]:
    """First and last day of ``today``'s calendar month."""
    start = today.replace(day=1)
    if start.month == 12:
        nxt = start.replace(year=start.year + 1, month=1)
    else:
        nxt = start.replace(month=start.month + 1)
    return start, nxt - timedelta(days=1)


async def next_invoice_number(db: AsyncSession, year: int) -> str:
    """Gapless sequential invoice number for ``year`` (e.g. "2026-000001").

    Bumped by an atomic upsert-increment inside the caller's transaction, so it
    serializes concurrent issuers and a rollback leaves no gap (see
    SequenceCounter). The human format is subject to the [LEGAL_REVIEW] sign-off.
    """
    seq = (await db.execute(
        text("SELECT public.next_invoice_sequence(:year)"), {"year": year},
    )).scalar_one()
    return f"{year}-{int(seq):06d}"


async def generate_invoice(
    db: AsyncSession,
    tenant: Tenant,
    *,
    period_start: date | None = None,
    period_end: date | None = None,
    issued_at: datetime | None = None,
) -> Invoice | None:
    """Create (and add) one invoice for the tenant's current plan + period.

    Idempotent per (tenant, period_start): returns ``None`` if one was already
    issued for that period (nothing generated) so that ``run_billing_cycle`` counts
    only NEWLY-issued invoices, not pre-existing ones on a re-run. Trial tenants are
    not billed (also ``None``).
    """
    if tenant.plan_code == "trial":
        return None

    today = tashkent_today(issued_at)
    if period_start is None or period_end is None:
        period_start, period_end = _period_bounds(today)

    already_issued = (await db.execute(
        select(Invoice.id).where(
            Invoice.tenant_id == tenant.id,
            Invoice.period_start == period_start,
        )
    )).scalar_one_or_none()
    if already_issued is not None:
        # Already billed for this period — nothing to generate (idempotent no-op).
        return None

    from app.services.plan_limits import effective_config
    cfg = await effective_config(db, tenant)
    if cfg.plan is None:
        log.warning("billing: no active plan %s for tenant %s",
                    tenant.plan_code, tenant.id)
        return None

    active_count = await active_installment_count(db, tenant.id)
    c_ov = cfg.overage.get("contract") or {}
    amount, line_items = compute_amount(
        cfg.plan, active_count,
        price_monthly=cfg.price_monthly,
        contract_overage=c_ov,
        included_contracts=cfg.limits.get("contract"),
    )

    issued = issued_at or datetime.now(UTC)
    inv = Invoice(
        tenant_id=tenant.id,
        plan_code=tenant.plan_code,
        invoice_number=await next_invoice_number(db, period_start.year),
        period_start=period_start,
        period_end=period_end,
        active_installments_count=active_count,
        unit_price=(c_ov.get("price") if c_ov.get("enabled") else None),
        amount=amount,
        status=InvoiceStatus.open,
        line_items=line_items,
        issued_at=issued,
        due_at=issued + timedelta(days=DUE_NET_DAYS),
    )
    db.add(inv)
    # (#196) The tenant now has a billed (paid) subscription — award any pending
    # referral to whoever referred this owner. Idempotent + gated (dormant unless the
    # referral programme is enabled), so it's a cheap early-return that moves no money
    # otherwise, and never double-pays (at most one Referral per referee tenant).
    from app.services.referral import maybe_award_referral_conversion
    await maybe_award_referral_conversion(db, tenant)
    # (#246) Apply any prepaid balance to this + prior open invoices, so a tenant who
    # paid (the R37 Click/Payme top-up) isn't dunned. Flush so the new invoice is
    # visible to the settle query.
    await db.flush()
    # (#854) Lock the tenant row before settle's balance read-modify-write. run_billing_cycle
    # loads the tenant with a plain (unlocked) SELECT, so without this a concurrent top-up —
    # mark_topup_paid, which DOES lock the tenant — could have its just-committed credit
    # clobbered by this settle's stale write (lost top-up). The billing cron is
    # single_instance and a top-up only ever locks its own tenant, so this single lock
    # can't deadlock. refresh is safe: generate_invoice makes no pending change to `tenant`
    # itself (the referral award above credits a DIFFERENT tenant), so nothing is discarded.
    await db.refresh(tenant, with_for_update=True)
    await settle_invoices_from_balance(db, tenant)
    return inv


async def settle_invoices_from_balance(db: AsyncSession, tenant: Tenant) -> int:
    """(#246) Apply the tenant's prepaid balance to its OPEN/OVERDUE invoices, oldest
    first — marking each ``paid`` and deducting the amount — so paying via Click/Payme
    (the R37 balance top-up) actually clears invoices and stops the dunning ladder.
    Returns the count settled. Whole-invoice only (no partial payments): settles while
    the balance fully covers the next invoice. The caller commits.

    (F-22) Self-protecting: the candidate invoices are selected ``FOR UPDATE`` and each
    is re-checked still-unpaid under that lock before being flipped, so the function is
    safe even if a caller forgets to lock the tenant row first. Previously safety rested
    entirely on the caller convention (both current callers ``refresh(tenant,
    with_for_update=True)`` first); a new "mark invoice paid" path that skipped the
    tenant lock could otherwise double-settle an invoice. We still only deduct the
    balance by the invoices THIS call actually transitions, so two racing settlers
    settle disjoint invoice sets and never double-spend the same one."""
    bal = tenant.balance or Decimal("0")
    if bal <= 0:
        return 0
    invoices = (await db.execute(
        select(Invoice).where(
            Invoice.tenant_id == tenant.id,
            Invoice.status.in_([InvoiceStatus.open, InvoiceStatus.overdue]),
        ).order_by(Invoice.period_start.asc())
        .with_for_update()
    )).scalars().all()
    settled = 0
    now = datetime.now(UTC)
    for inv in invoices:
        # (F-22) Re-check under the row lock: a concurrent settler may have flipped
        # this invoice to paid while we waited for the lock. Only transition — and
        # only deduct balance for — invoices still genuinely open/overdue.
        if inv.status not in (InvoiceStatus.open, InvoiceStatus.overdue):
            continue
        if bal < inv.amount:
            break
        bal -= inv.amount
        inv.status = InvoiceStatus.paid
        inv.paid_at = now
        inv.payment_method = inv.payment_method or "balance"
        db.add(TenantBalanceTxn(
            tenant_id=tenant.id, amount=-inv.amount, kind="invoice_settlement",
            note=f"Invoice {inv.invoice_number}", balance_after=bal,
            created_by_user_id=None,
        ))
        settled += 1
    tenant.balance = bal
    # (#861) Clearing every outstanding invoice lifts a non-payment suspension/block.
    # run_dunning only does the forward transitions and nothing else reactivates a tenant
    # on payment, so without this a tenant who tops up and settles their dues stays locked
    # out until a plan-change (which also re-charges) or an admin intervenes. Mirrors the
    # reactivation tenant_plan_change performs (active + cleared suspended_at). Partial
    # settlement (some invoices still unpaid) must NOT reactivate. Both callers hold the
    # tenant row FOR UPDATE (mark_topup_paid; generate_invoice since #854), so this is safe.
    #
    # (F-22) Decide reactivation on "no candidate left open/overdue" rather than
    # ``settled == len(invoices)``: with the under-lock re-check a candidate a
    # concurrent settler already flipped to paid is correctly NOT counted as still
    # outstanding, while one we couldn't afford (the ``bal < amount`` break) is.
    any_outstanding = any(
        inv.status in (InvoiceStatus.open, InvoiceStatus.overdue) for inv in invoices
    )
    if invoices and not any_outstanding and tenant.status in (
        TenantStatus.suspended, TenantStatus.blocked,
    ):
        tenant.status = TenantStatus.active
        tenant.suspended_at = None
    return settled


# ── plan-change suggestion ───────────────────────────────────────────────────

async def projected_monthly(db: AsyncSession, tenant: Tenant) -> dict | None:
    """Current projected monthly charge (base + contract overage) for the tenant,
    plus a per-contract breakdown. ``None`` for trial tenants."""
    from app.services.plan_limits import effective_config
    cfg = await effective_config(db, tenant)
    if cfg.plan is None:
        return None
    active_count = await active_installment_count(db, tenant.id)
    c_ov = cfg.overage.get("contract") or {}
    amount, line_items = compute_amount(
        cfg.plan, active_count,
        price_monthly=cfg.price_monthly,
        contract_overage=c_ov,
        included_contracts=cfg.limits.get("contract"),
    )
    return {
        "amount": str(amount),
        "active_count": active_count,
        "included_contracts": cfg.limits.get("contract"),
        "contract_overage_price": int(c_ov.get("price") or 0) if c_ov.get("enabled") else None,
        "line_items": line_items,
    }


async def suggest_upgrade(db: AsyncSession, tenant: Tenant) -> dict | None:
    """If the tenant's projected monthly cost (with overage) exceeds a higher,
    public, fixed-price plan that would cover their current usage, recommend it.

    Generic — no plan-name hardcoding. Returns the cheapest qualifying plan.
    """
    proj = await projected_monthly(db, tenant)
    if proj is None:
        return None
    projected = Decimal(proj["amount"])
    active_count = int(proj["active_count"])

    plans = (await db.execute(
        select(Plan).where(
            Plan.is_active.is_(True), Plan.is_public.is_(True),
            Plan.is_contact_sales.is_(False),
        ).order_by(Plan.price_monthly)
    )).scalars().all()

    best: Plan | None = None
    for p in plans:
        if p.code == tenant.plan_code:
            continue
        price = Decimal(p.price_monthly or 0)
        # Only recommend a plan that is cheaper than the current projection and
        # whose included contract quota covers current usage (no overage needed).
        covers = p.max_active_installments is None or active_count <= p.max_active_installments
        if price < projected and covers and (best is None or price < Decimal(best.price_monthly or 0)):
            best = p
    if best is None:
        return None
    return {
        "to_plan": best.code,
        "to_plan_name": best.name_uz,
        "current_projected": str(round_amount(projected)),
        "plan_price": str(round_amount(Decimal(best.price_monthly or 0))),
        "message": "Siz uchun Business yoki Pro tarifiga o'tish foydaliroq bo'lishi mumkin.",
    }


# Back-compat alias (the tenant billing endpoint historically imported this).
suggest_custom_switch = suggest_upgrade


# ── cron entry points ────────────────────────────────────────────────────────

async def run_billing_cycle(db: AsyncSession, *, today: date | None = None) -> int:
    """Issue this month's invoice for every billable tenant. Returns count."""
    today = today or tashkent_today()
    period_start, period_end = _period_bounds(today)
    tenants = (await db.execute(
        select(Tenant).where(
            Tenant.plan_code != "trial",
            Tenant.status.in_([TenantStatus.active, TenantStatus.suspended]),
        )
    )).scalars().all()

    issued = 0
    for t in tenants:
        try:
            inv = await generate_invoice(
                db, t, period_start=period_start, period_end=period_end,
            )
            if inv is not None:
                # Commit per tenant so the unique(tenant, period) constraint
                # only rolls back this one tenant on a duplicate, not the cycle.
                await db.commit()
                issued += 1
        except IntegrityError:
            # Another run already issued this tenant's invoice for the period.
            await db.rollback()
    return issued


async def run_dunning(db: AsyncSession, *, now: datetime | None = None) -> dict:
    """Advance overdue invoices and suspend/block delinquent tenants.

    Returns a small summary dict for logging. Idempotent — safe to run daily.
    """
    now = now or datetime.now(UTC)

    # 1. open + past due → overdue. (F-19) Set-based UPDATE — no per-row load of the
    #    whole platform's open invoices into Python just to flip a status.
    flipped = (await db.execute(
        update(Invoice)
        .where(Invoice.status == InvoiceStatus.open, Invoice.due_at < now)
        .values(status=InvoiceStatus.overdue)
    )).rowcount or 0  # type: ignore[attr-defined]  # rowcount lives on CursorResult

    # 2. overdue invoices drive tenant status. (F-19) Compute the oldest unpaid
    #    due_at per tenant with a SQL GROUP BY + MIN aggregate (instead of loading
    #    every overdue invoice and reducing in Python), and page the per-tenant
    #    ladder in keyset batches ordered by tenant_id so memory stays bounded.
    suspended = blocked = 0
    #: (#MUDD-111) The ladder used to move a shop from working to suspended to
    #: blocked in total silence — the owner discovered it by opening the app and
    #: finding the gate. Only COLLECTED here; the caller sends after it commits
    #: (`run_dunning_job`). Sending inside this function would hold the
    #: `with_for_update()` tenant row locks open across a Telegram round-trip
    #: per owner, and would let a Telegram timeout abort a status change that is
    #: already correct.
    notify_transitions: list[tuple[UUID, TenantStatus, int]] = []
    last_tenant_id: UUID | None = None
    while True:
        agg = select(
            Invoice.tenant_id,
            func.min(Invoice.due_at).label("oldest_due"),
        ).where(Invoice.status == InvoiceStatus.overdue)
        if last_tenant_id is not None:
            agg = agg.where(Invoice.tenant_id > last_tenant_id)
        rows = (await db.execute(
            agg.group_by(Invoice.tenant_id)
            .order_by(Invoice.tenant_id.asc())
            .limit(_DUNNING_BATCH)
        )).all()
        if not rows:
            break
        for tenant_id, due in rows:
            # Lock the tenant row for the status read-modify-write (a top-up may be
            # racing to reactivate it; serialize on the row).
            tenant = (await db.execute(
                select(Tenant).where(Tenant.id == tenant_id).with_for_update()
            )).scalar_one_or_none()
            if tenant is None:
                continue
            days_overdue = (now - due).days
            if days_overdue >= BLOCK_AFTER_DUE_DAYS and tenant.status != TenantStatus.blocked:
                tenant.status = TenantStatus.blocked
                blocked += 1
                notify_transitions.append((tenant.id, TenantStatus.blocked, days_overdue))
            elif (
                days_overdue >= SUSPEND_AFTER_DUE_DAYS
                and tenant.status == TenantStatus.active
            ):
                tenant.status = TenantStatus.suspended
                tenant.suspended_at = now
                suspended += 1
                notify_transitions.append((tenant.id, TenantStatus.suspended, days_overdue))
        last_tenant_id = rows[-1][0]
        if len(rows) < _DUNNING_BATCH:
            break

    return {
        "flipped_overdue": flipped,
        "suspended": suspended,
        "blocked": blocked,
        # Consumed by `run_dunning_job` once the transaction has committed.
        "transitions": notify_transitions,
    }


async def notify_dunning_transitions(
    db: AsyncSession,
    transitions: list[tuple[UUID, TenantStatus, int]],
) -> int:
    """Tell each shop owner their account just changed state, and how to fix it.

    Sent once per transition rather than once per day: the caller only appends
    when the status ACTUALLY flipped (`active` → `suspended`, anything →
    `blocked`), and those guards make the flip a one-time event. That is why
    there is no dedup row here — re-running the cron the same day re-reads a
    tenant that is already suspended and appends nothing.

    Delivery failures are counted but never raised. Dunning is an accounting
    operation; a shop whose owner has blocked the bot must still be suspended.
    """
    if not transitions:
        return 0

    from app.config import settings
    from app.models import TenantUser, User
    from app.models.enums import TenantRole
    from app.services.notifications import (
        MiniAppButton,
        billing_deeplink,
        send_to_telegram,
    )

    pay_button = MiniAppButton(
        label="💳 To'lov qilish",
        # Routable even for a blocked tenant — see the blocked branch in
        # `frontend/src/routes/router.tsx`, which keeps `billing` mounted
        # precisely so this is not a dead link for the people who need it most.
        mini_app_url=f"{settings.frontend_url.rstrip('/')}/uz/tenant/billing",
        deep_link=billing_deeplink(),
    )

    sent = 0
    for tenant_id, new_status, days_overdue in transitions:
        if new_status is TenantStatus.blocked:
            text_body = (
                "<b>Hisobingiz to'xtatildi</b>\n"
                f"To'lov {days_overdue} kun kechikdi. Do'kon ishi to'liq "
                "to'xtatildi.\n\n"
                "To'lovni amalga oshirsangiz, hisob darhol tiklanadi."
            )
        else:
            text_body = (
                "<b>Hisobingiz vaqtincha cheklandi</b>\n"
                f"To'lov {days_overdue} kun kechikdi.\n\n"
                "To'lov qilmasangiz, hisob butunlay to'xtatiladi."
            )
        owners = (await db.execute(
            select(User.telegram_id)
            .join(TenantUser, TenantUser.user_id == User.id)
            .where(
                TenantUser.tenant_id == tenant_id,
                TenantUser.role == TenantRole.owner,
                TenantUser.is_active.is_(True),
            )
        )).all()
        for (tg_id,) in owners:
            if tg_id and await send_to_telegram(tg_id, text_body, mini_app=pay_button):
                sent += 1
    return sent
