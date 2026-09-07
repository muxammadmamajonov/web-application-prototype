"""Streaming PDF endpoints — contracts + receipts.

Auth model
----------
* Contract: a tenant member (owner/manager/sales) OR the customer whose
  installment it is can download. Customer auth is via the existing JWT
  (Telegram OAuth path) — we look up the customer by `telegram_id`.
* Receipt: same rules as contract; the Payment's installment is the gate.

Why streaming
-------------
WeasyPrint renders into memory (bytes). For our typical contract (~80 KB
PDF) the streaming wrapper is cosmetic — but it keeps the door open to
chunked-render upgrades without an API change.
"""
from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.tenant import _require_owner_or_manager, resolve_tenant
from app.core.ratelimit import pdf_limit
from app.core.security import current_user
from app.db import get_db
from app.models import (
    Customer,
    Installment,
    Payment,
    Tenant,
    TenantSettings,
    TenantUser,
    User,
)

router = APIRouter(tags=["pdf"])
log = logging.getLogger("muddato.pdf.api")


# ── helpers ──────────────────────────────────────────────────────────────

async def _can_access_installment(
    db: AsyncSession, *, user: User, installment_id: UUID,
) -> Installment:
    """Allow if user is a member of the installment's tenant OR is the customer.

    (Batch 5) This is reached from routes that identify the caller but never
    resolve a tenant — the contract PDF, the signed download, the deep link — so
    it first narrows the request to the tenant that owns THIS installment.
    ``scope_to_object`` only narrows: a caller that already has a tenant scope
    keeps it, and an installment belonging to anyone else stays invisible, which
    is the same uniform 404 this function already returns.
    """
    from app.core.dbauth import scope_to_object

    await scope_to_object(db, "installment", installment_id)
    inst = (await db.execute(
        select(Installment).where(Installment.id == installment_id)
    )).scalar_one_or_none()
    if inst is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "installment_not_found")

    # Tenant member?
    member = (await db.execute(
        select(TenantUser).where(
            TenantUser.tenant_id == inst.tenant_id,
            TenantUser.user_id == user.id,
            TenantUser.is_active.is_(True),
        )
    )).scalar_one_or_none()
    if member is not None:
        return inst

    # Customer? A Telegram link alone is not enough to hand over a contract:
    # links exist that predate phone proof, and an invitation link can be
    # forwarded. The same gate the rest of the customer API uses.
    from app.api.me import identity_is_provable

    if user.telegram_id is not None and identity_is_provable(user):
        customer = (await db.execute(
            select(Customer).where(Customer.id == inst.customer_id)
        )).scalar_one_or_none()
        if customer is not None and customer.telegram_id == user.telegram_id:
            return inst

    # (#89) Uniform 404 — a 403 here reveals the installment exists to a
    # non-owner (existence oracle). Indistinguishable from "not found".
    raise HTTPException(status.HTTP_404_NOT_FOUND, "installment_not_found")


# ── endpoints ────────────────────────────────────────────────────────────

@router.get("/tenant/installments/{installment_id}/contract.pdf", dependencies=[pdf_limit()])
async def installment_contract_pdf(
    installment_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Contract PDF for the installment. Streams `application/pdf`."""
    from app.services.pdf import render_contract_pdf

    await _can_access_installment(db, user=user, installment_id=installment_id)
    # (MUDD-139) Access is not the same question as visibility. `sales` may open
    # this contract (they made the sale) but MUDD-55 does not let them read the
    # customer's passport / JSHSHIR / birthdate / address — which this document
    # prints verbatim. One definition, in app.core.pii_policy.
    from app.core.pii_policy import contract_pii_masked_for

    mask = await contract_pii_masked_for(db, user=user, installment_id=installment_id)
    try:
        pdf_bytes = await render_contract_pdf(db, installment_id, mask_pii=mask)
    except Exception:
        log.exception("pdf.contract_render_failed installment=%s", installment_id)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "pdf_render_failed") from None
    headers = {
        "Content-Disposition": f'inline; filename="contract-{installment_id}.pdf"',
        "Cache-Control": "private, no-store",
    }
    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)


@router.get("/tenant/payments/{payment_id}/receipt.pdf", dependencies=[pdf_limit()])
async def payment_receipt_pdf(
    payment_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Receipt PDF for a single payment."""
    from app.services.pdf import render_receipt_pdf

    pay = (await db.execute(select(Payment).where(Payment.id == payment_id))).scalar_one_or_none()
    if pay is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "payment_not_found")
    # Reuse installment-level access check.
    await _can_access_installment(db, user=user, installment_id=pay.installment_id)

    try:
        pdf_bytes = await render_receipt_pdf(db, payment_id)
    except Exception:
        log.exception("pdf.receipt_render_failed payment=%s", payment_id)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "pdf_render_failed") from None
    headers = {
        "Content-Disposition": f'inline; filename="receipt-{payment_id}.pdf"',
        "Cache-Control": "private, no-store",
    }
    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)


@router.get("/tenant/invoices/{invoice_id}/invoice.pdf", dependencies=[pdf_limit()])
async def tenant_invoice_pdf(
    invoice_id: UUID,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Subscription invoice PDF — owner/manager only, scoped to the requesting tenant.

    The role gate matches `GET /api/tenant/invoices`: an invoice is the shop's
    *SaaS billing* relationship with the platform, not an operational document.
    The sibling contract and receipt PDFs above deliberately keep object-level
    access instead — a clerk and the customer legitimately need those.

    The guard runs before the lookup so a denied role cannot use the 403/404
    split to probe whether an invoice id exists.
    """
    from app.models import Invoice
    from app.services.pdf import render_invoice_pdf

    tenant, membership = ctx
    _require_owner_or_manager(membership)
    inv = (await db.execute(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invoice_not_found")
    try:
        pdf_bytes = await render_invoice_pdf(db, invoice_id)
    except Exception:
        log.exception("pdf.invoice_render_failed invoice=%s", invoice_id)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "pdf_render_failed") from None
    headers = {
        "Content-Disposition": f'inline; filename="invoice-{invoice_id}.pdf"',
        "Cache-Control": "private, no-store",
    }
    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)


# ── tenant contract template editor (Pro+) ────────────────────────────────

class ContractTemplateIn(BaseModel):
    html: str


@router.put("/tenant/settings/contract-template")
async def update_contract_template(
    body: ContractTemplateIn,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Pro+ only — upload custom contract HTML. Validated via the same
    rules `render_contract_pdf` enforces, so a saved template always
    renders deterministically.
    """
    from app.models import Plan
    from app.services.pdf import TemplateValidationError, validate_custom_template

    tenant, membership = ctx
    _require_owner_or_manager(membership)

    # Plan gate — the plan's OWN capability flag decides, never its code.
    #
    # This used to load the plan, look at `allow_pdf_template_upload`, and then
    # `pass` — throwing the answer away — before gating on
    # `plan_code not in ("pro", "custom")`. Its comment said the flag was still
    # to come in "Round 26", but the column already exists on Plan and an
    # operator sets it per plan in the plan editor.
    #
    # Plan codes stopped being a fixed enum in R36 and the catalogue moved on,
    # so that literal had quietly become wrong in both directions. Measured
    # against the live plans table:
    #
    #     starter     allow=False   code-gate denied    correct by luck
    #     business    allow=True    code-gate DENIED    wrong — plan grants it
    #     pro         allow=True    code-gate allowed   correct
    #     enterprise  allow=True    code-gate DENIED    wrong — the top tier
    #     flex        allow=False   code-gate denied    correct by luck
    #
    # `custom` had not existed for some time, so that arm of the tuple matched
    # nothing at all. Reading the flag grants business and enterprise the
    # capability their plan record already says they have, and takes it from
    # nobody: starter and flex stay denied, pro stays allowed.
    #
    # An unknown plan code (a tenant stamped with one the catalogue lacks) has
    # no row, so `plan` is None and this denies — the safe direction for a paid
    # capability, and never a silent grant.
    plan = (await db.execute(select(Plan).where(Plan.code == tenant.plan_code))).scalar_one_or_none()
    if not (plan and plan.allow_pdf_template_upload):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "plan_does_not_allow_custom_pdf")

    try:
        validate_custom_template(body.html)
    except TemplateValidationError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    s = (await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if s is None:
        s = TenantSettings(tenant_id=tenant.id)
        db.add(s)
        await db.flush()
    s.contract_template_html = body.html
    await db.commit()
    return {"ok": True, "length": len(body.html)}


@router.delete("/tenant/settings/contract-template", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contract_template(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Reset to the bundled default template."""
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    s = (await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if s and s.contract_template_html:
        s.contract_template_html = None
        await db.commit()


@router.get("/tenant/settings/contract-template/preview.pdf", dependencies=[pdf_limit()])
async def preview_contract_template(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Render the current saved template against the most recent active
    installment for this tenant. Lets owners check formatting after an
    edit without dragging out a real customer record."""
    from app.services.pdf import render_contract_pdf, render_contract_preview
    tenant, membership = ctx
    _require_owner_or_manager(membership)

    inst = (await db.execute(
        select(Installment)
        .where(Installment.tenant_id == tenant.id)
        .order_by(Installment.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if inst is None:
        # (#54) Fresh tenant with no installment yet → render the template against
        # sample data so the owner can still preview formatting (was a hard 404).
        pdf_bytes = await render_contract_preview(db, tenant.id)
    else:
        pdf_bytes = await render_contract_pdf(db, inst.id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="contract-preview.pdf"'},
    )
