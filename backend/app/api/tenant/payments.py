"""Tenant back-office: customer payments and their approval queue."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.currency import (
    ints_by_currency,
    money_by_currency,
    primary_total,
    tenant_primary_currency,
)
from app.core.security import current_user
from app.db import get_db
from app.models import (
    Customer,
    Installment,
    Payment,
    Tenant,
    TenantUser,
    User,
)
from app.models.enums import (
    PaymentMethod,
    PaymentStatus,
    TenantRole,
)
from app.schemas.common import LimitQ, OffsetQ
from app.services.payment_review_notify import retire_now_bg, schedule_retirement

from ._shared import (
    _require_owner_or_manager,
    resolve_tenant,
)

router = APIRouter()

# ── payments ───────────────────────────────────────────────────────────────

class PaymentIn(BaseModel):
    installment_id:    UUID
    # >0, and strictly inside what the column can hold: payments.amount is
    # NUMERIC(14,2), so 10^12 itself does NOT fit. The old `le=10**12` admitted
    # exactly that value, which then overflowed on apply and left a payment
    # stuck pending behind a permanent 500 (QA M-1).
    amount:            Decimal = Field(gt=0, le=Decimal("999999999999"))
    method:            PaymentMethod
    receipt_image_url: str | None = None
    notes:             str | None = None
    # When an owner/manager records a payment directly they can mark it approved
    # in one step (staff submissions stay pending for owner review).
    mark_approved:     bool = False

def _payment_dict(p: Payment, customer_name: str | None = None,
                  contract_number: str | None = None) -> dict:
    return {
        "id":                str(p.id),
        "installment_id":    str(p.installment_id),
        "customer_id":       str(p.customer_id),
        "customer_name":     customer_name,
        # The confirmation must name the contract the money lands on, not just
        # the customer — a customer can hold several.
        "contract_number":   contract_number,
        "amount":            int(p.amount or 0),
        # The payment's own currency — equal to its contract's, held so
        # by `fk_payments_contract_currency`.
        "currency":          p.currency,
        "method":            p.method.value,
        "status":            p.status.value,
        "receipt_image_url": p.receipt_image_url,
        "paid_at":           p.paid_at.isoformat() if p.paid_at else None,
        "created_at":        p.created_at.isoformat(),
    }

@router.get("/payments")
async def list_payments(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: str | None = None,
    limit: LimitQ = 50, offset: OffsetQ = 0,
) -> dict:
    tenant, _ = ctx
    # `created_at` is a transaction timestamp, not a unique key — two payments
    # recorded in the same instant tie on it, and an unstable sort under
    # OFFSET/LIMIT can then show one on two pages, or on none (MUDD-84). `id`
    # breaks every tie, mirroring the worklist's own pagination contract, so
    # paging through a status is a true partition of the tenant's history.
    q = select(Payment).where(Payment.tenant_id == tenant.id).order_by(
        Payment.created_at.desc(), Payment.id.desc(),
    )
    if status_filter:
        try:
            q = q.where(Payment.status == PaymentStatus(status_filter))
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_status") from None
    rows = (await db.execute(q.offset(offset).limit(limit))).scalars().all()
    # (#139) Count the FILTERED query so the total matches the status filter.
    total = (await db.execute(
        select(func.count()).select_from(q.order_by(None).subquery())
    )).scalar_one()
    # The per-status breakdown for the WHOLE tenant, independent of
    # `status_filter` — so a UI showing "Rad etilgan (12)" tabs alongside the
    # currently filtered page reads real server counts, not a count derived
    # from whatever page happens to be loaded client-side (MUDD-84).
    status_counts = {s.value: 0 for s in PaymentStatus}
    for st, cnt in (await db.execute(
        select(Payment.status, func.count())
        .where(Payment.tenant_id == tenant.id)
        .group_by(Payment.status)
    )).all():
        status_counts[st.value] = cnt
    # Resolve customer names so the UI shows who paid, not a UUID.
    names: dict = {}
    cust_ids = {p.customer_id for p in rows if p.customer_id}
    if cust_ids:
        for c in (await db.execute(select(Customer).where(Customer.id.in_(cust_ids)))).scalars():
            names[c.id] = c.full_name
    # Contract numbers for the same page of rows — one bounded IN-query over the
    # page's installment ids, mirroring the customer-name lookup above. Not an
    # N+1: the id set comes from the already-fetched page.
    contracts: dict = {}
    inst_ids = {p.installment_id for p in rows if p.installment_id}
    if inst_ids:
        for i_id, c_no in (await db.execute(
            select(Installment.id, Installment.contract_number)
            .where(Installment.id.in_(inst_ids), Installment.tenant_id == tenant.id)
        )).all():
            contracts[i_id] = c_no
    return {
        "items": [
            _payment_dict(p, names.get(p.customer_id), contracts.get(p.installment_id))
            for p in rows
        ],
        "total": total,
        "counts": status_counts,
    }

@router.get("/staff-today")
async def staff_today(
    user: Annotated[User, Depends(current_user)],
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """A salesperson's shift snapshot: what *they* collected today, the shop's
    pending-approval queue, and customers added today."""
    tenant, _ = ctx
    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    # (MUDD-114) Grouped by currency: a shift that took both UZS and USD would
    # otherwise report their sum as one figure under one label, and this is the
    # number a salesperson reconciles their cash drawer against.
    received_by_currency = money_by_currency((await db.execute(
        select(Payment.currency, func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.tenant_id == tenant.id,
            Payment.submitted_by_user_id == user.id,
            Payment.created_at >= day_start,
        ).group_by(Payment.currency)
    )).all())
    primary = await tenant_primary_currency(db, tenant.id)
    pending = (await db.execute(
        select(func.count(Payment.id)).where(
            Payment.tenant_id == tenant.id, Payment.status == PaymentStatus.pending,
        )
    )).scalar_one()
    new_customers = (await db.execute(
        select(func.count(Customer.id)).where(
            Customer.tenant_id == tenant.id, Customer.created_at >= day_start,
        )
    )).scalar_one()
    return {
        "currency": primary,
        "received_today": int(primary_total(received_by_currency, primary)),
        "received_today_by_currency": ints_by_currency(received_by_currency),
        "pending_count": int(pending or 0),
        "new_customers_today": int(new_customers or 0),
    }

@router.post("/payments", status_code=status.HTTP_201_CREATED)
async def create_payment(
    body: PaymentIn,
    user: Annotated[User, Depends(current_user)],
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    tenant, membership = ctx

    # (MUDD-74) The frontend has sent this header since MUDD-56; the server
    # never read it, so a double-tap or network retry wrote a second Payment
    # row for the same money. The advisory lock is scoped to THIS (tenant, key)
    # pair — unlike create_installment's per-tenant lock — so concurrent
    # payments with different keys aren't serialized against each other; it's
    # held until this request commits or rolls back, so a concurrent replay
    # with the SAME key blocks here until the first request's row is visible.
    # uq_payments_idem is the durable DB backstop.
    if idempotency_key is not None:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
            {"k": f"payment_idem:{tenant.id}:{idempotency_key}"},
        )
        existing = (await db.execute(
            select(Payment).where(
                Payment.tenant_id == tenant.id,
                Payment.idempotency_key == idempotency_key,
            )
        )).scalar_one_or_none()
        if existing is not None:
            return _payment_dict(existing)

    ins = (await db.execute(
        select(Installment).where(Installment.id == body.installment_id, Installment.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if ins is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "installment_not_found")

    from app.services.installments import accepts_payment
    if not accepts_payment(ins):
        raise HTTPException(status.HTTP_409_CONFLICT, "installment_closed")

    # (QA H-1) Refuse more money than the contract owes, BEFORE a row exists.
    # Rejecting up front also means an amount the numeric column cannot hold
    # never becomes a pending payment that 500s on every approval attempt and
    # can be neither approved nor cleared (QA M-1).
    from app.services.installments import overpayment_excess
    if overpayment_excess(ins, body.amount):
        raise HTTPException(status.HTTP_409_CONFLICT, "payment_exceeds_balance")

    # Owners/managers can record-and-approve in one step; everyone else (sales)
    # submits a pending payment the owner reviews.
    approve = body.mark_approved and membership.role in (TenantRole.owner, TenantRole.manager)
    now = datetime.now(UTC)
    payment = Payment(
        tenant_id=tenant.id, installment_id=ins.id, customer_id=ins.customer_id,
        currency=ins.currency,           # from the contract, never the shop
        amount=body.amount, method=body.method,
        status=PaymentStatus.approved if approve else PaymentStatus.pending,
        receipt_image_url=body.receipt_image_url, notes=body.notes,
        submitted_by_user_id=user.id, paid_at=now,
        approved_by_user_id=user.id if approve else None,
        approved_at=now if approve else None,
        idempotency_key=idempotency_key,
    )
    db.add(payment)

    if approve:
        from app.services.installments import apply_payment
        # (#86) Lock the installment row before applying — exactly like
        # approve_payment — so two concurrent record-and-approve calls on the
        # same installment can't interleave on paid_amount and lose an update.
        ins = (await db.execute(
            select(Installment).where(Installment.id == ins.id).with_for_update()
        )).scalar_one()
        # Re-checked under the lock: the balance read above was unlocked, so a
        # concurrent payment could have settled the loan in between.
        if overpayment_excess(ins, payment.amount or Decimal("0")):
            raise HTTPException(status.HTTP_409_CONFLICT, "payment_exceeds_balance")
        apply_payment(ins, payment.amount or Decimal("0"), now=now)
        from app.services import audit
        await audit.record(
            db, action="payment.record_approved", tenant_id=tenant.id,
            actor_user_id=user.id, entity_type="payment", entity_id=str(ins.id),
            after={"amount": int(payment.amount or 0), "installment_id": str(ins.id)},
        )

    await db.commit()
    await db.refresh(payment)
    return _payment_dict(payment)

@router.get("/payments/{payment_id}/review")
async def payment_review(
    payment_id: UUID,
    user: Annotated[User, Depends(current_user)],
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Everything a reviewer must see BEFORE deciding on a payment.

    The Telegram notification's button lands here, and landing here approves
    nothing — that is the whole point of the split. A one-tap approve from a
    chat message is a money decision taken without looking at the evidence, so
    the button opens the evidence and the decision stays a separate, explicitly
    authenticated action.

    The tenant scope is in the WHERE clause rather than checked afterwards, so a
    payment belonging to another shop is indistinguishable from one that does
    not exist. A reviewer probing ids learns nothing either way.
    """
    tenant, membership = ctx
    # Sales staff take payments; they do not adjudicate them. Same rule as
    # approve/reject, applied at the point where the receipt becomes visible —
    # otherwise the evidence is readable by someone who cannot act on it.
    _require_owner_or_manager(membership)

    payment = (await db.execute(
        select(Payment).where(Payment.id == payment_id, Payment.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "payment_not_found")

    ins = (await db.execute(
        select(Installment).where(Installment.id == payment.installment_id)
    )).scalar_one()
    customer = (await db.execute(
        select(Customer).where(Customer.id == payment.customer_id)
    )).scalar_one_or_none()

    from app.core.documents import (
        long_datetime,
        money,
        payment_method_label,
        payment_status_label,
    )

    locale = user.locale or "uz"
    return {
        "id": str(payment.id),
        "customer_name": customer.full_name if customer else None,
        "contract_number": ins.contract_number,
        "installment_id": str(ins.id),
        # The amount and its currency come from the immutable payment row.
        "amount": str(payment.amount or 0),
        "amount_formatted": money(payment.amount or 0, payment.currency),
        "currency": payment.currency,
        "method": payment.method.value,
        "method_label": payment_method_label(payment.method, locale),
        "status": payment.status.value,
        "status_label": payment_status_label(payment.status, locale),
        "receipt_image_url": payment.receipt_image_url,
        "submitted_at": payment.created_at.isoformat() if payment.created_at else None,
        "submitted_at_label": long_datetime(payment.created_at, locale),
        # Whether the ACTIONS should be offered at all. A processed payment
        # cannot be processed twice, and a button that still looks live after a
        # final decision is how a reviewer ends up filing a support ticket.
        "actionable": payment.status == PaymentStatus.pending,
    }


@router.post("/payments/{payment_id}/approve")
async def approve_payment(
    payment_id: UUID,
    background: BackgroundTasks,
    user: Annotated[User, Depends(current_user)],
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    # (audit/sec) Lock the payment row and re-check status under the lock — two
    # concurrent approvals would otherwise both read status=pending on unlocked
    # snapshots, both pass the guard, and both apply_payment → double credit.
    payment = (await db.execute(
        select(Payment).where(Payment.id == payment_id, Payment.tenant_id == tenant.id)
        .with_for_update()
    )).scalar_one_or_none()
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "payment_not_found")
    if payment.status != PaymentStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, "not_pending")
    now = datetime.now(UTC)
    payment.status = PaymentStatus.approved
    payment.approved_by_user_id = user.id
    payment.approved_at = now
    if payment.paid_at is None:
        payment.paid_at = now

    ins = (await db.execute(
        select(Installment).where(Installment.id == payment.installment_id).with_for_update()
    )).scalar_one()
    from app.services.installments import accepts_payment, apply_payment, overpayment_excess
    if not accepts_payment(ins):
        raise HTTPException(status.HTTP_409_CONFLICT, "installment_closed")
    # (QA H-1) A pending payment can be approved long after it was submitted, by
    # which time other payments may have settled the loan. Checking only at
    # submission would let a stale approval overshoot the balance — so the
    # authoritative check is here, under the row lock.
    if overpayment_excess(ins, payment.amount or Decimal("0")):
        raise HTTPException(status.HTTP_409_CONFLICT, "payment_exceeds_balance")
    apply_payment(ins, payment.amount or Decimal("0"), now=now)

    # Durable in the SAME transaction that commits the decision: a restart can
    # now delay retirement, never lose it.
    notify_chat = await schedule_retirement(
        db, payment_id=payment.id, outcome="approved", locale=(user.locale or "uz"),
    ) > 0

    from app.services import audit
    await audit.record(
        db, action="payment.approve", tenant_id=tenant.id,
        actor_user_id=user.id, entity_type="payment", entity_id=str(payment.id),
        after={"amount": int(payment.amount or 0), "installment_id": str(ins.id)},
    )
    await db.commit()
    # The decision is now durable. Retiring the chat action runs afterwards and
    # in the background: a Telegram failure must not undo an approval, and a
    # Telegram round trip must not sit on the request path of a money decision.
    if notify_chat:
        background.add_task(retire_now_bg, payment_id=payment.id)
    await db.refresh(payment)
    await db.refresh(ins)
    # Approving a payment changes what the customer owes, so the response
    # carries the contract's post-apply balance (additively — existing payment
    # fields are unchanged) and the success message can name the resulting debt
    # instead of a bare "approved".
    return {
        **_payment_dict(payment, contract_number=ins.contract_number),
        "installment": {
            "id":             str(ins.id),
            "contract_number": ins.contract_number,
            "total_amount":   int(ins.total_amount or 0),
            "paid_amount":    int(ins.paid_amount or 0),
            "remaining":      max(0, int(ins.principal or 0) - int(ins.paid_amount or 0)),
            "status":         ins.status.value,
            "next_due_date":  ins.next_due_date.isoformat() if ins.next_due_date else None,
        },
    }

@router.post("/payments/{payment_id}/reject")
async def reject_payment(
    payment_id: UUID,
    background: BackgroundTasks,
    user: Annotated[User, Depends(current_user)],
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    payment = (await db.execute(
        select(Payment).where(Payment.id == payment_id, Payment.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "payment_not_found")
    if payment.status != PaymentStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, "not_pending")
    payment.status = PaymentStatus.rejected
    payment.approved_by_user_id = user.id   # records the actor regardless of outcome
    payment.approved_at = datetime.now(UTC)
    from app.services import audit
    await audit.record(
        db, action="payment.reject", tenant_id=tenant.id,
        actor_user_id=user.id, entity_type="payment", entity_id=str(payment.id),
    )
    notify_chat = await schedule_retirement(
        db, payment_id=payment.id, outcome="rejected", locale=(user.locale or "uz"),
    ) > 0
    await db.commit()
    if notify_chat:
        background.add_task(retire_now_bg, payment_id=payment.id)
    await db.refresh(payment)
    return _payment_dict(payment)
