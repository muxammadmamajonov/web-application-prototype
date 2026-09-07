"""Tenant back-office: the shop overview, its customers and guarantors, KYC media,
IMEI lookup and cross-tenant risk signals."""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Response,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import invite_codes
from app.core.currency import (
    ints_by_currency,
    money_by_currency,
    primary_total,
    tenant_primary_currency,
)
from app.core.ratelimit import export_limit, invite_limit, search_limit_if_query
from app.core.validators import UzPhone, UzPhoneOpt
from app.db import get_db
from app.models import (
    Customer,
    Guarantor,
    Installment,
    InstallmentItem,
    Payment,
    Product,
    Tenant,
    TenantUser,
    User,
)
from app.models.enums import (
    CustomerStatus,
    InstallmentStatus,
    PaymentStatus,
    TenantRole,
)
from app.schemas.common import LimitQ, OffsetQ

from ._shared import (
    _MAX_MONEY,
    CustomerIn,
    _derive_passport_id,
    _require_owner,
    _require_owner_or_manager,
    _tenant_today,
    resolve_tenant,
)

router = APIRouter()

# ── overview ───────────────────────────────────────────────────────────────

@router.get("/overview")
async def tenant_overview(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant, _ = ctx

    async def _compute() -> dict:
        # (MUDD-114) Every money KPI here is GROUPED BY currency. A shop may
        # change its configured currency at any time and that relabels nothing, so a
        # tenant holding both UZS and USD contracts is ordinary — and the old
        # un-grouped `func.sum` added those together, then the dashboard printed
        # the result under whichever single currency the client happened to pick.
        # Nothing is converted: there is no rate source, and inventing one would
        # turn a visible wrong number into an invisible one.
        primary = await tenant_primary_currency(db, tenant.id)

        debt_by_currency = money_by_currency((await db.execute(
            select(Installment.currency, func.coalesce(func.sum(Installment.remaining_balance), 0))
            .where(Installment.tenant_id == tenant.id, Installment.status.in_([
                InstallmentStatus.active, InstallmentStatus.overdue,
            ]))
            .group_by(Installment.currency)
        )).all())
        overdue_by_currency = money_by_currency((await db.execute(
            select(Installment.currency, func.coalesce(func.sum(Installment.remaining_balance), 0))
            .where(Installment.tenant_id == tenant.id, Installment.status == InstallmentStatus.overdue)
            .group_by(Installment.currency)
        )).all())
        # The tenant's today, not the server's — otherwise this KPI disagrees
        # with the collections queue rendered directly beneath it.
        due_today_by_currency = money_by_currency((await db.execute(
            select(Installment.currency, func.coalesce(func.sum(Installment.monthly_amount), 0))
            .where(Installment.tenant_id == tenant.id,
                   Installment.next_due_date == _tenant_today(tenant))
            .group_by(Installment.currency)
        )).all())
        pending_receipts = (await db.execute(
            select(func.count(Payment.id))
            .where(Payment.tenant_id == tenant.id, Payment.status == PaymentStatus.pending)
        )).scalar_one()

        status_counts_raw = (await db.execute(
            select(Installment.status, func.count(Installment.id))
            .where(Installment.tenant_id == tenant.id)
            .group_by(Installment.status)
        )).all()
        by_status = {row[0].value: row[1] for row in status_counts_raw}

        # (MUDD-83) What the shop actually sells, by kind. Count AND money:
        # "23 phone contracts" and "phones are 61% of the book" are different
        # questions, and a shop deciding what to stock needs the second one.
        # (MUDD-141) And neither is UNITS: a contract selling 5 phones on one
        # line is one contract, not five, but it moved five phones. `units_sold`
        # (`sum(qty)`) and `contract_count` (`count(distinct Installment.id)`)
        # answer different questions and both are kept — see `reports.products()`
        # for the same split.
        #
        # `line_total`, not the contract total: a contract can mix a phone and
        # a fridge, and attributing its whole value to either would double-count
        # across the breakdown. Summing the lines keeps the parts adding up.
        #
        # Cancelled contracts are excluded — they are not sales. `reports.products()`
        # draws the same line, and a breakdown that disagreed with the report
        # would just make both untrustworthy.
        # (MUDD-114) Also grouped by currency — a category breakdown that added
        # a USD phone to a UZS phone would rank the categories by a number that
        # is not a quantity of anything. The ordering uses the PRIMARY currency's
        # amount, because "which category is biggest" only has an answer within
        # one unit; a category that exists solely in a secondary currency still
        # appears, after the ranked ones. `units_sold` and `contract_count` are
        # unit-free counts, so they add across currency groups without issue —
        # `Installment.currency` is single-valued per contract, so a contract
        # never lands in two currency groups for the same category and is never
        # double-counted.
        category_raw = (await db.execute(
            select(
                InstallmentItem.category,
                Installment.currency,
                func.count(func.distinct(Installment.id)),
                func.coalesce(func.sum(InstallmentItem.qty), 0),
                func.sum(InstallmentItem.line_total),
            )
            .join(Installment, Installment.id == InstallmentItem.installment_id)
            .where(
                Installment.tenant_id == tenant.id,
                Installment.status != InstallmentStatus.cancelled,
                InstallmentItem.category.is_not(None),
            )
            .group_by(InstallmentItem.category, Installment.currency)
        )).all()
        cat_acc: dict[str, dict[str, Any]] = {}
        for slug, cur, cnt, qty, amount in category_raw:
            entry = cat_acc.setdefault(
                slug, {"category": slug, "contract_count": 0, "units_sold": 0, "amounts": []},
            )
            entry["contract_count"] += int(cnt or 0)
            entry["units_sold"] += int(qty or 0)
            entry["amounts"].append((cur, amount))
        by_category = sorted(
            (
                {
                    "category": e["category"],
                    "units_sold": e["units_sold"],
                    "contract_count": e["contract_count"],
                    # Legacy key == contract_count. Old clients and the 30s
                    # overview cache still read `count` as the contract tally —
                    # never drop it, and never let it drift from contract_count.
                    "count": e["contract_count"],
                    "amount_by_currency": ints_by_currency(money_by_currency(e["amounts"])),
                    # Legacy key. Now the PRIMARY-currency amount rather than a
                    # cross-currency sum, so the unit the UI prints beside it is
                    # finally the unit the number is in.
                    "amount_uzs": int(primary_total(money_by_currency(e["amounts"]), primary)),
                }
                for e in cat_acc.values()
            ),
            key=lambda r: r["amount_uzs"],
            reverse=True,
        )

        cust_count = (await db.execute(
            select(func.count(Customer.id)).where(Customer.tenant_id == tenant.id)
        )).scalar_one()
        prod_count = (await db.execute(
            select(func.count(Product.id)).where(
                Product.tenant_id == tenant.id, Product.is_active.is_(True),
            )
        )).scalar_one()

        return {
            "tenant": {"id": str(tenant.id), "name": tenant.name, "status": tenant.status.value},
            # (MUDD-114) The `_uzs` keys are KEPT so no client breaks, but they
            # no longer lie: each is the total in `primary_currency`, which is
            # the unit the dashboard labels them with. The full picture is in the
            # `*_by_currency` maps beside them — a shop with a second currency
            # now sees it stated instead of silently added in.
            "primary_currency": primary,
            "debt_total_uzs": int(primary_total(debt_by_currency, primary)),
            "overdue_uzs": int(primary_total(overdue_by_currency, primary)),
            "due_today_uzs": int(primary_total(due_today_by_currency, primary)),
            "debt_total_by_currency": ints_by_currency(debt_by_currency),
            "overdue_by_currency": ints_by_currency(overdue_by_currency),
            "due_today_by_currency": ints_by_currency(due_today_by_currency),
            "pending_receipts": pending_receipts,
            "customers_count": cust_count,
            "active_products_count": prod_count,
            "installments_by_status": by_status,
            # A LIST, not a dict keyed by slug: the order is the answer here
            # (biggest category first), and JSON object key order is not
            # something a client should have to rely on.
            "installments_by_category": by_category,
        }

    # (#256) Read-through cache: this dashboard aggregate runs 7 COUNT/SUM queries
    # on every load and tolerates ~30s of staleness (it's a glanceable summary,
    # not a money-authoritative read). Keyed per tenant; fail-open on Redis fault.
    from app.core.cache import cached_json
    return await cached_json(
        f"overview:{tenant.id}", ttl=30, compute=_compute, label="tenant_overview",
    )

class CustomerPatch(BaseModel):
    full_name:       str | None = Field(default=None, min_length=2, max_length=120)
    phone:           UzPhoneOpt = Field(default=None, min_length=4, max_length=32)
    secondary_phone: UzPhoneOpt = Field(default=None, max_length=32)
    passport_series: str | None = Field(default=None, max_length=8)
    passport_number: str | None = Field(default=None, max_length=16)
    passport_id:     str | None = Field(default=None, max_length=32)
    jshshir:         str | None = Field(default=None, min_length=14, max_length=14, pattern=r"^\d{14}$")
    birthdate:       date | None = None
    address:         str | None = Field(default=None, max_length=500)
    notes:           str | None = Field(default=None, max_length=1000)
    # (#85) status + telegram_id are intentionally NOT patchable here: status
    # changes must go through the audited approve/reject endpoints, and
    # telegram_id is identity (set only via the Telegram link flow). Exposing
    # them let a PATCH bypass moderation + audit and attach an arbitrary id.
    employment_place: str | None = Field(default=None, max_length=200)
    monthly_income:   Decimal | None = Field(default=None, ge=0, le=_MAX_MONEY)
    mehnat_daftarchasi_photo_urls: list[str] | None = None

def _proxy_kyc(urls: list[str] | None, tenant_id: object) -> list[str]:
    """Rewrite stored KYC media values — a private ``KycBlob`` id (#113) or a
    legacy public URL — into authenticated, tenant-bound app-proxy URLs so a
    passport/ID scan is never returned to the client as a fetchable link
    (#107/#113). Idempotent: an empty or already-proxied value passes through."""
    from urllib.parse import quote

    from app.services.media import make_kyc_ref

    out: list[str] = []
    for v in urls or []:
        if not v or v.startswith("/api/"):
            out.append(v)
            continue
        ref = make_kyc_ref(value=v, tenant_id=str(tenant_id))
        out.append(f"/api/tenant/kyc-media?ref={quote(ref, safe='')}")
    return out

@router.get("/kyc-media")
async def kyc_media(
    ref: str,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Authenticated, tenant-bound proxy for a KYC photo (#107/#113).

    The media value is encrypted inside ``ref`` and bound to the owning tenant, so
    an anonymous caller or another tenant's member cannot fetch a passport/ID scan.
    New media is a PRIVATE ``KycBlob`` (Postgres or a private bucket); legacy
    values are public archive.org URLs still streamed server-side until backfilled.
    """
    from app.services.media import read_kyc_ref

    tenant, membership = ctx
    # (MUDD-55 H2 #3) A passport/ID scan is the same PII class as the masked
    # passport_* fields in _customer_dict/_guarantor_dict — gating the text
    # fields is meaningless if the raw scan behind them is still one GET away
    # for any role.
    _require_owner_or_manager(membership)
    parsed = read_kyc_ref(ref)
    if parsed is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_ref")
    tid, value = parsed
    if tid != str(tenant.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "wrong_tenant")

    headers = {"Cache-Control": "private, max-age=300"}

    # Legacy public archive.org URL (pre-#113): stream server-side until backfilled.
    if value.startswith(("http://", "https://")):
        import httpx

        from app.core.urlsafety import UnsafeUrlError, fetch_with_safe_redirects

        # (#170) SSRF guard: the ref value is tenant-writable, so restrict fetches
        # to the archive.org family (where legacy KYC media lives) — an arbitrary or
        # internal host must never be reachable. archive.org redirects to its own
        # CDN (*.archive.org), so in-family redirects must keep working.
        #
        # Redirects are resolved MANUALLY (`follow_redirects=False` inside the
        # helper): with automatic following the allowlist would be applied only to
        # this first URL, and an open redirect on an allowed host would carry the
        # fetch to an internal address whose body we then hand back to the caller.
        # Every hop is re-validated — scheme, credentials, host allowlist and the
        # resolved addresses — by the same predicate the PDF renderer uses.
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await fetch_with_safe_redirects(
                    client, value, allowed_hosts=("archive.org",), max_redirects=3,
                )
        except UnsafeUrlError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "media_host_not_allowed") from None
        except httpx.HTTPError:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "fetch_failed") from None
        if resp.status_code != 200:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "media_not_found")
        return Response(
            content=resp.content,
            media_type=resp.headers.get("content-type", "application/octet-stream"),
            headers=headers,
        )

    # New private KycBlob: load by id, re-check tenant ownership, serve the bytes.
    from uuid import UUID as _UUID

    from app.models import KycBlob

    try:
        blob_id = _UUID(value)
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_ref") from None
    blob = (await db.execute(
        select(KycBlob).where(KycBlob.id == blob_id, KycBlob.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if blob is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "media_not_found")
    if blob.object_key:
        from app.services.receipt_storage import get_receipt_store
        store = get_receipt_store()
        if store is None:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "kyc_store_unavailable")
        got = await store.get(blob.object_key)
        if got is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "media_not_found")
        content, ct = got
    else:
        content, ct = (blob.data or b""), blob.content_type
    return Response(content=content, media_type=ct, headers=headers)

# (MUDD-55 H2 R2) Sentinel written by _customer_dict/_guarantor_dict for a
# masked field. Any value made up entirely of asterisks is treated the same
# way on write — see _is_masked_pii — so a client that round-trips a masked
# GET response into a PATCH can never overwrite the real value.
_PII_MASK = "***"

def _is_masked_pii(v: object) -> bool:
    return isinstance(v, str) and v != "" and set(v) == {"*"}

# (MUDD-55 review — endpoint guard was too broad) `_require_owner_or_manager`
# on the whole PATCH/POST blocks a sales rep from the intake/contact work they
# still need to do (create a guarantor, fix a phone number) — see the R1-shaped
# review that blocked PR #951. The fields below are exactly the ones
# _customer_dict/_guarantor_dict mask on read; anything else in the same patch
# still applies. Keep these two lists in lockstep with the masking in those
# two functions.
_CUSTOMER_PII_FIELDS = (
    "passport_series", "passport_number", "passport_id", "jshshir",
    "birthdate", "address", "monthly_income", "mehnat_daftarchasi_photo_urls",
)
_GUARANTOR_PII_FIELDS = ("passport_series", "passport_number", "jshshir", "address", "photo_urls")

def _customer_dict(c: Customer, known: dict[str, int] | None = None, role: TenantRole | None = None) -> dict:
    """``known`` is the batch lookup from `customer_identity.known_telegram_ids`.
    Omitting it yields ``telegram_status`` of joined/new only — correct but
    unable to distinguish "never seen" from "already uses the bot", so list
    endpoints should always pass it.

    MUDD-25 H2: Mask sensitive PII (passport, jshshir, monthly_income, address)
    unless the caller is an owner or manager. Sales staff and other roles see
    masked values to enforce KYC data access controls.

    MUDD-55 H2 #8/decision: ``secondary_phone``, ``notes`` and
    ``employment_place`` are intentionally left unmasked. They're contact and
    day-to-day sales context (who to call, what was discussed, where the
    customer works), not KYC/credit-decision data — unlike passport/jshshir/
    address/monthly_income, which exist to verify identity and assess
    creditworthiness. Masking them would block sales staff from doing the
    outreach and collections work this role exists for, without closing a
    comparable identity-fraud or credit-risk exposure.
    """
    from app.services import customer_identity

    # Check if caller can see full PII
    can_see_full_pii = role in (TenantRole.owner, TenantRole.manager)

    return {
        "telegram_status": customer_identity.status_for(c, known or {}),
        "id":              str(c.id),
        "full_name":       c.full_name,
        "phone":           c.phone,
        "secondary_phone": c.secondary_phone,
        "passport_series": c.passport_series if can_see_full_pii else (_PII_MASK if c.passport_series else None),
        "passport_number": c.passport_number if can_see_full_pii else (_PII_MASK if c.passport_number else None),
        "passport_id":     c.passport_id if can_see_full_pii else (_PII_MASK if c.passport_id else None),
        "jshshir":         c.jshshir if can_see_full_pii else (_PII_MASK if c.jshshir else None),
        "birthdate":       c.birthdate.isoformat() if c.birthdate and can_see_full_pii else None,
        "address":         c.address if can_see_full_pii else (_PII_MASK if c.address else None),
        "status":          c.status.value,
        "telegram_id":     c.telegram_id,
        "notes":           c.notes,
        "employment_place": c.employment_place,
        "monthly_income":  int(c.monthly_income) if c.monthly_income is not None and can_see_full_pii else None,
        # (MUDD-55 H2 #4) The scan behind these URLs is now gated by role at
        # /kyc-media (403 for non owner/manager), but a masked customer record
        # still must not advertise the links themselves — showing a fetchable
        # (if 403'd) URL beside masked passport fields defeats the point of
        # masking them in the first place.
        "mehnat_daftarchasi_photo_urls": (
            _proxy_kyc(c.mehnat_daftarchasi_photo_urls, c.tenant_id) if can_see_full_pii else []
        ),
        "pii_masked":      not can_see_full_pii,
        "created_at":      c.created_at.isoformat(),
    }

@router.get("/customers", dependencies=[search_limit_if_query()])
async def list_customers(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str | None = None,
    status_filter: str | None = None,
    limit: LimitQ = 50, offset: OffsetQ = 0,
) -> dict:
    tenant, membership = ctx
    base = select(Customer).where(Customer.tenant_id == tenant.id)
    if status_filter:
        try:
            base = base.where(Customer.status == CustomerStatus(status_filter))
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_status") from None
    if q:
        # One search box covers the plaintext identity fields. The government-ID
        # columns (passport_*, jshshir) are encrypted at rest (#72) and so are
        # not substring-searchable — a LIKE would scan ciphertext and match
        # nothing — so they're intentionally excluded here.
        term = q.strip().lower()
        like = f"%{term}%"
        digits = "".join(ch for ch in term if ch.isdigit())
        conds = [
            func.lower(Customer.full_name).like(like),
            func.lower(func.coalesce(Customer.phone, "")).like(like),
            func.lower(func.coalesce(Customer.secondary_phone, "")).like(like),
            func.lower(func.coalesce(Customer.address, "")).like(like),
        ]
        if digits:
            dlike = f"%{digits}%"
            conds += [
                func.coalesce(Customer.phone, "").like(dlike),
                func.coalesce(Customer.secondary_phone, "").like(dlike),
            ]
        base = base.where(or_(*conds))
    rows = (await db.execute(
        base.order_by(Customer.created_at.desc()).offset(offset).limit(limit)
    )).scalars().all()
    # Total reflects the active filters so the header count + pagination match what
    # the user is actually looking at (not the whole unfiltered tenant book).
    count_q = select(func.count()).select_from(base.order_by(None).subquery())
    total = (await db.execute(count_q)).scalar_one()
    # One indexed lookup for the whole page, not one per row (see
    # customer_identity: the `IN` runs over the same expression that backs
    # uq_users_phone_digits).
    from app.services import customer_identity
    known = await customer_identity.known_telegram_ids(db, [c.phone for c in rows])
    return {
        "items": [_customer_dict(c, known, membership.role) for c in rows],
        "total": total, "limit": limit, "offset": offset,
    }

# ── customer moderation (auto_approve_customers=false flow) ────────────────

@router.post("/customers/{cid}/approve")
async def approve_customer(
    cid: UUID,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Flip a self-onboarded ``pending`` customer to ``active``.

    Called from the dashboard's "Yangi mijoz" moderation queue. Owners and
    managers only — sales staff can sell to approved customers but can't
    promote new ones.
    """
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    c = (await db.execute(
        select(Customer).where(Customer.id == cid, Customer.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer_not_found")
    if c.status != CustomerStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, "not_pending")
    c.status = CustomerStatus.active
    from app.services import audit
    user = (await db.execute(select(User).where(User.id == membership.user_id))).scalar_one()
    await audit.record(
        db, action="customer.approve", tenant_id=tenant.id,
        actor_user_id=user.id, entity_type="customer", entity_id=str(c.id),
    )
    await db.commit()
    await db.refresh(c)
    return _customer_dict(c, None, membership.role)

@router.post("/customers/{cid}/reject")
async def reject_customer(
    cid: UUID,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Reject a self-onboarded customer — flips them to ``blocked``.

    We keep the row (rather than deleting) so future invitation attempts
    from the same Telegram account can be matched against their history.
    """
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    c = (await db.execute(
        select(Customer).where(Customer.id == cid, Customer.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer_not_found")
    if c.status != CustomerStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, "not_pending")
    c.status = CustomerStatus.blocked
    from app.services import audit
    user = (await db.execute(select(User).where(User.id == membership.user_id))).scalar_one()
    await audit.record(
        db, action="customer.reject", tenant_id=tenant.id,
        actor_user_id=user.id, entity_type="customer", entity_id=str(c.id),
    )
    await db.commit()
    await db.refresh(c)
    return _customer_dict(c, None, membership.role)

@router.get("/customers/{cid}")
async def get_customer(
    cid: UUID,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant, membership = ctx
    c = (await db.execute(
        select(Customer).where(Customer.id == cid, Customer.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer_not_found")
    return _customer_dict(c, None, membership.role)

async def _mint_customer_invite(
    db: AsyncSession, tenant: Tenant, customer_id: UUID,
    membership: TenantUser | None = None,
) -> dict:
    """Mint a one-time invite bound to a specific customer. When the joiner opens
    it, their Telegram id is attached to THIS customer (see invitations.claim),
    which is what gates new contracts on the customer having joined. Returns the
    token + canonical web URL; the caller commits.

    (Batch 6) ``membership`` is the authorization, and it is checked HERE rather
    than in each caller — both of this function's callers previously minted a
    live bearer credential with no role check at all, and a third caller would
    have inherited the same nothing. ``require_may_mint`` denies any kind it has
    no explicit rule for, so a future invitation type cannot arrive unguarded.

    It stays keyword-optional only so an internal caller with no HTTP membership
    (there is none today) fails loudly at the policy rather than at the signature.
    """
    from datetime import timedelta

    from app.api.invitations import _tenant_bot_username
    from app.core.invite_policy import MintSurface, require_may_mint
    from app.models import Invitation

    require_may_mint(membership, "customer", surface=MintSurface.intake)

    # (Batch 6) Re-issuing must REPLACE, not accumulate. Three resends used to
    # leave three independently usable 30-day credentials for one customer, and
    # the shop had no way to see that: the newest link works, so the screen looks
    # correct while the older ones stay live in whatever chat they were pasted
    # into. Expiring the previous ones is the same thing the owner-claim path in
    # app/api/platform/tenants.py already does before minting a replacement.
    #
    # Expired rather than deleted, so "which links existed, and who minted them"
    # survives — that history is the point of the attribution below.
    stale = (await db.execute(
        select(Invitation).where(
            Invitation.tenant_id == tenant.id,
            Invitation.customer_id == customer_id,
            Invitation.used_at.is_(None),
            Invitation.expires_at > datetime.now(UTC),
        )
    )).scalars().all()
    for prior in stale:
        prior.expires_at = datetime.now(UTC)

    token, token_verifier = await invite_codes.mint(db)
    db.add(Invitation(
        token_verifier=token_verifier, tenant_id=tenant.id, kind="customer",
        customer_id=customer_id,
        # (Batch 6) Attribution. A bearer credential that can be forwarded out of
        # the shop is not something a role check can prevent — the row is
        # genuinely the minter's to see — so the control is knowing who issued it.
        created_by_user_id=getattr(membership, "user_id", None),
        expires_at=datetime.now(UTC) + timedelta(days=30),
    ))
    await db.flush()
    bot = await _tenant_bot_username(db, tenant)
    # Primary link is the BOT deep link (opens the Mini-App in Telegram);
    # web_url is a browser fallback. Both come from the shared builder so this
    # hand-off cannot drift from the scheme regular invites use.
    return {
        "token": token,
        "url": invite_codes.deep_link(bot, token),
        "web_url": invite_codes.web_url(token),
    }

@router.post("/customers", status_code=status.HTTP_201_CREATED)
async def create_customer(
    body: CustomerIn,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant, membership = ctx
    data = body.model_dump(exclude_unset=True)
    data["passport_id"] = _derive_passport_id(
        body.passport_series, body.passport_number, body.passport_id,
    )
    customer = Customer(tenant_id=tenant.id, status=CustomerStatus.active, **data)
    db.add(customer)
    await db.flush()
    # If the person whose number this is has already proved they own it, the
    # record arrives connected. Otherwise the shop is told to chase a customer
    # who is standing in front of them, and the customer is asked to prove a
    # number they proved months ago.
    #
    # Answers about THIS tenant only, and returns nothing about the matched
    # account — the shop learns its own record is linked, not who else exists.
    from app.services.identity_link import NEEDS_OPERATOR, link_new_customer
    link = await link_new_customer(db, customer=customer)
    # (#168) National-ID data collected → record the customer's KYC-processing consent
    # (lawful basis = consent; the operator collects it from the customer at intake).
    if customer.passport_id or customer.jshshir:
        from app.services.consent import record_consent
        await record_consent(
            db, subject_kind="customer", subject_id=customer.id, kind="kyc_processing",
            lawful_basis="consent", tenant_id=tenant.id, source="kyc",
        )
    from app.services import customer_identity, customer_outreach

    # An invitation is minted ONLY when there is somebody left to invite. A
    # customer who was just linked to a proved identity is already connected —
    # handing the shop a one-time link for them would ask a person to join
    # something they are in, and would put a live bearer credential on a screen
    # for no reason.
    invite = None
    notified = False
    if link.ok:
        status_ = customer_identity.JOINED
    elif link.state in NEEDS_OPERATOR:
        # Reconciliation needs a human. Not connected, and not pretending to be;
        # the invitation is still offered so the shop is not stuck meanwhile.
        status_ = link.state.value
        invite = await _mint_customer_invite(db, tenant, customer.id, membership)
    else:
        status_ = customer_identity.NEW
        invite = await _mint_customer_invite(db, tenant, customer.id, membership)

    await db.commit()
    await db.refresh(customer)

    # If this person has PROVED the number, deliver the invitation to them
    # directly rather than handing the operator a link to pass on somehow.
    if invite is not None:
        owner = await customer_identity.find_verified_user(db, customer.phone)
        if owner is not None and owner.telegram_id is not None:
            status_ = customer_identity.KNOWN
            notified = await customer_outreach.notify_customer_invited(
                db, tenant=tenant, customer=customer, app_url=invite["url"],
            )
    return {
        **_customer_dict(customer, None, membership.role), "invite": invite,
        "telegram_status": status_, "notified": notified,
    }

@router.post("/customers/{cid}/invite", status_code=status.HTTP_201_CREATED, dependencies=[invite_limit()])
async def resend_customer_invite(
    cid: UUID,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Re-issue a one-time invite for an existing customer (e.g. one created
    before this flow, or a lost link) so they can join via Telegram."""
    tenant, membership = ctx
    c = (await db.execute(
        select(Customer).where(Customer.id == cid, Customer.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer_not_found")
    # Nothing to re-issue for somebody who is already connected. Minting one
    # would put a live bearer credential on a screen for a link nobody needs —
    # and a forwarded copy of it is exactly the exposure the claim path refuses.
    if c.telegram_id is not None:
        return {"already_linked": True, "telegram_status": "joined", "invite": None}
    invite = await _mint_customer_invite(db, tenant, c.id, membership)
    await db.commit()
    return invite

@router.patch("/customers/{cid}")
async def update_customer(
    cid: UUID,
    body: CustomerPatch,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant, membership = ctx
    # (MUDD-55 H2 #7, revised after review) Read and write were asymmetric:
    # sales could not see PII via GET but could still overwrite it via PATCH.
    # The first fix gated the whole endpoint on owner/manager, but that also
    # blocked sales from the non-PII edits (phone, notes) they legitimately
    # make — see the R1-shaped review on PR #951. Gate at the FIELD level
    # instead, matching exactly what _customer_dict masks on read.
    c = (await db.execute(
        select(Customer).where(Customer.id == cid, Customer.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer_not_found")
    data = body.model_dump(exclude_unset=True)
    # (MUDD-55 H2 R2) A form pre-filled from the masked GET response ("***")
    # must not be allowed to round-trip that sentinel back into the DB — that
    # would silently destroy the real KYC value. Drop any masked field from
    # the patch instead of writing it; every other field in the same request
    # still applies normally.
    for field in ("passport_series", "passport_number", "passport_id", "jshshir", "address"):
        if field in data and _is_masked_pii(data[field]):
            data.pop(field)
    can_see_full_pii = membership.role in (TenantRole.owner, TenantRole.manager)
    if not can_see_full_pii:
        for field in _CUSTOMER_PII_FIELDS:
            data.pop(field, None)
    for k, v in data.items():
        setattr(c, k, v)
    # Re-derive combined passport_id if series/number provided.
    if "passport_series" in data or "passport_number" in data:
        c.passport_id = _derive_passport_id(c.passport_series, c.passport_number, c.passport_id)
    await db.commit()
    await db.refresh(c)
    return _customer_dict(c, None, membership.role)

@router.delete("/customers/{cid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_customer(
    cid: UUID,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    c = (await db.execute(
        select(Customer).where(Customer.id == cid, Customer.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer_not_found")
    # Soft via status change: if there are active installments, refuse.
    open_ins = (await db.execute(
        select(func.count(Installment.id)).where(
            Installment.customer_id == c.id,
            Installment.status.in_([InstallmentStatus.active, InstallmentStatus.overdue]),
        )
    )).scalar_one()
    if open_ins:
        raise HTTPException(status.HTTP_409_CONFLICT, "customer_has_open_installments")
    # (#172) Purge KYC photos + receipt images (object-store bytes + rows) so the hard
    # delete doesn't orphan the most sensitive PII in storage.
    from app.services.privacy import purge_customer_media
    await purge_customer_media(db, c)
    await db.delete(c)
    await db.commit()

@router.get("/customers/{cid}/export", dependencies=[export_limit()])
async def export_customer_data(
    cid: UUID,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Owner — export everything linked to a customer as a JSON bundle, to fulfil a
    data-subject access request (#167)."""
    tenant, membership = ctx
    _require_owner(membership)
    c = (await db.execute(
        select(Customer).where(Customer.id == cid, Customer.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer_not_found")
    from app.services.privacy import export_customer
    return await export_customer(db, c)

@router.post("/customers/{cid}/erase")
async def erase_customer_data(
    cid: UUID,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Owner — erase a customer's PII (right to erasure, #167). The customer is
    anonymized in place: financial records (installments/payments) are kept but
    every PII field is wiped, private KYC/receipt images are deleted, and
    SMS/notification/invitation PII is purged. Irreversible."""
    tenant, membership = ctx
    _require_owner(membership)
    c = (await db.execute(
        select(Customer).where(Customer.id == cid, Customer.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer_not_found")
    from app.services.privacy import erase_customer
    counts = await erase_customer(db, c, actor_user_id=membership.user_id)
    await db.commit()
    return {"erased": True, "purged": counts}

# ── guarantors (kafil) ───────────────────────────────────────────────────

class GuarantorIn(BaseModel):
    full_name:       str = Field(min_length=2, max_length=120)
    phone:           UzPhone = Field(min_length=4, max_length=32)
    passport_series: str | None = Field(default=None, max_length=8)
    passport_number: str | None = Field(default=None, max_length=16)
    jshshir:         str | None = Field(default=None, min_length=14, max_length=14, pattern=r"^\d{14}$")
    relationship:    str | None = Field(default=None, max_length=64)
    telegram_id:     int | None = None
    address:         str | None = Field(default=None, max_length=500)
    photo_urls:      list[str] = Field(default_factory=list)

class GuarantorPatch(BaseModel):
    full_name:       str | None = Field(default=None, min_length=2, max_length=120)
    phone:           UzPhoneOpt = Field(default=None, min_length=4, max_length=32)
    passport_series: str | None = Field(default=None, max_length=8)
    passport_number: str | None = Field(default=None, max_length=16)
    jshshir:         str | None = Field(default=None, min_length=14, max_length=14, pattern=r"^\d{14}$")
    relationship:    str | None = Field(default=None, max_length=64)
    telegram_id:     int | None = None
    address:         str | None = Field(default=None, max_length=500)
    photo_urls:      list[str] | None = None

def _guarantor_dict(g: Guarantor, role: TenantRole | None = None) -> dict:
    """Same masking contract as ``_customer_dict``: a guarantor is a natural
    person with the same PII class (passport, jshshir, address, ID scans) as
    the customer whose contract they're backing, so it must not be readable
    through a second, unguarded model just because it's a different table
    (MUDD-55 H2 #1)."""
    can_see_full_pii = role in (TenantRole.owner, TenantRole.manager)
    return {
        "id":              str(g.id),
        "full_name":       g.full_name,
        "phone":           g.phone,
        "passport_series": g.passport_series if can_see_full_pii else (_PII_MASK if g.passport_series else None),
        "passport_number": g.passport_number if can_see_full_pii else (_PII_MASK if g.passport_number else None),
        "jshshir":         g.jshshir if can_see_full_pii else (_PII_MASK if g.jshshir else None),
        "relationship":    g.relationship_label,
        "telegram_id":     g.telegram_id,
        "address":         g.address if can_see_full_pii else (_PII_MASK if g.address else None),
        "photo_urls":      _proxy_kyc(g.photo_urls, g.tenant_id) if can_see_full_pii else [],
        "pii_masked":      not can_see_full_pii,
        "created_at":      g.created_at.isoformat(),
    }

@router.get("/guarantors")
async def list_guarantors(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str | None = None,
    limit: LimitQ = 50, offset: OffsetQ = 0,
) -> dict:
    tenant, membership = ctx
    base = select(Guarantor).where(Guarantor.tenant_id == tenant.id)
    if q:
        # jshshir is encrypted at rest (#72) and not substring-searchable; search
        # the plaintext name + phone only.
        like = f"%{q.lower()}%"
        base = base.where(
            func.lower(Guarantor.full_name).like(like)
            | Guarantor.phone.like(like)
        )
    rows = (await db.execute(
        base.order_by(Guarantor.created_at.desc()).offset(offset).limit(limit)
    )).scalars().all()
    total = (await db.execute(
        select(func.count(Guarantor.id)).where(Guarantor.tenant_id == tenant.id)
    )).scalar_one()
    return {"items": [_guarantor_dict(g, membership.role) for g in rows], "total": total}

@router.post("/guarantors", status_code=status.HTTP_201_CREATED)
async def create_guarantor(
    body: GuarantorIn,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant, membership = ctx
    # (MUDD-55 H2 #5, revised after review) A guarantor's PII is collected by
    # sales at intake — the same class of action as `create_customer` (also
    # unguarded, deliberately: see its comment). An endpoint-level guard here
    # 403's the last step of the contract wizard for the role that runs it;
    # the masking on read (`_guarantor_dict(g, role)`) is what actually
    # protects this data. See the R1-shaped review on PR #951.
    data = body.model_dump(exclude_unset=True)
    rel = data.pop("relationship", None)
    g = Guarantor(tenant_id=tenant.id, relationship_label=rel, **data)
    db.add(g)
    # (#87) A duplicate (tenant_id, phone) raises IntegrityError → the global
    # handler maps it to 409 guarantor_phone_exists. Previously a bare `except
    # Exception` here mislabeled ANY failure as a duplicate phone.
    await db.commit()
    await db.refresh(g)
    return _guarantor_dict(g, membership.role)

@router.patch("/guarantors/{gid}")
async def update_guarantor(
    gid: UUID,
    body: GuarantorPatch,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant, membership = ctx
    # (revised after review) Same field-level approach as update_customer:
    # sales can update non-PII fields (name, phone, relationship) but not
    # overwrite the passport/jshshir/address/photo PII _guarantor_dict masks
    # on read. See the R1-shaped review on PR #951.
    g = (await db.execute(
        select(Guarantor).where(Guarantor.id == gid, Guarantor.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if g is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "guarantor_not_found")
    data = body.model_dump(exclude_unset=True)
    if "relationship" in data:
        g.relationship_label = data.pop("relationship")
    # (MUDD-55 H2 R2) Same masked-sentinel guard as update_customer — a form
    # pre-filled from a masked GET must not be able to overwrite real PII.
    for field in ("passport_series", "passport_number", "address"):
        if field in data and _is_masked_pii(data[field]):
            data.pop(field)
    can_see_full_pii = membership.role in (TenantRole.owner, TenantRole.manager)
    if not can_see_full_pii:
        for field in _GUARANTOR_PII_FIELDS:
            data.pop(field, None)
    for k, v in data.items():
        setattr(g, k, v)
    await db.commit()
    await db.refresh(g)
    return _guarantor_dict(g, membership.role)

@router.delete("/guarantors/{gid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_guarantor(
    gid: UUID,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    g = (await db.execute(
        select(Guarantor).where(Guarantor.id == gid, Guarantor.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if g is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "guarantor_not_found")
    # (#172) Purge the guarantor's KYC photos from storage before the hard delete.
    from app.services.privacy import purge_guarantor_media
    await purge_guarantor_media(db, g)
    await db.delete(g)
    await db.commit()

# ── IMEI / serial-number lookup ──────────────────────────────────────────

@router.get("/imei-check")
async def imei_check(
    serial: str,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Check whether a serial / IMEI was already sold.

    Returns ``in_tenant`` (this shop — usually a hard duplicate) and
    ``cross_tenant`` (some other shop already sold this device — a soft
    warning that the unit may be re-sold / stolen). Cross-tenant counting
    never leaks which shop; only a boolean + count is returned.
    """
    tenant, _ = ctx
    s = serial.strip()
    if not s:
        return {"serial": s, "in_tenant": False, "cross_tenant_count": 0}
    in_tenant = (await db.execute(
        select(func.count(InstallmentItem.id))
        .join(Installment, Installment.id == InstallmentItem.installment_id)
        .where(Installment.tenant_id == tenant.id, InstallmentItem.serial_number == s)
    )).scalar_one()
    cross = (await db.execute(
        select(func.count(InstallmentItem.id))
        .join(Installment, Installment.id == InstallmentItem.installment_id)
        .where(Installment.tenant_id != tenant.id, InstallmentItem.serial_number == s)
    )).scalar_one()
    return {
        "serial": s,
        "in_tenant": in_tenant > 0,
        "in_tenant_count": int(in_tenant),
        "cross_tenant_count": int(cross),
    }

# ── cross-tenant risk network (Round 23) ────────────────────────────────────

class RiskReportIn(BaseModel):
    customer_id: UUID
    # Defaults to a fraud report — the only severity a tenant can raise by
    # hand. Auto signals (late_*) come from the daily job.
    severity: Literal["fraud_reported", "charged_off"] = "fraud_reported"
    note: str | None = Field(default=None, max_length=500)

@router.get("/risk/score")
async def risk_score(
    customer_id: UUID,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Risk profile for one of this tenant's customers — internal history +
    (if opted in) aggregated external signals. No source tenant is leaked."""
    from app.services import risk as risk_svc
    tenant, membership = ctx
    # (MUDD-55 H2 #6) Credit-risk scoring is derived from the same JSHSHIR-keyed
    # KYC data the rest of this file gates behind owner/manager.
    _require_owner_or_manager(membership)
    customer = (await db.execute(
        select(Customer).where(Customer.id == customer_id, Customer.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer_not_found")
    return await risk_svc.score(db, tenant_id=tenant.id, customer=customer)

@router.post("/risk/report", status_code=status.HTTP_201_CREATED)
async def risk_report(
    body: RiskReportIn,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Manually flag a customer (e.g. confirmed fraud). Published immediately
    (no peer review yet — every report is audit-logged). Owner/manager only,
    and only meaningful for opted-in tenants with a JSHSHIR on file."""
    from app.models.enums import RiskSeverity, RiskSignalSource, RiskSignalStatus
    from app.services import audit
    from app.services import risk as risk_svc
    tenant, membership = ctx
    _require_owner_or_manager(membership)

    customer = (await db.execute(
        select(Customer).where(Customer.id == body.customer_id, Customer.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer_not_found")
    if risk_svc.hash_jshshir(customer.jshshir) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "customer_has_no_jshshir")

    sig = await risk_svc.upsert_signal(
        db,
        tenant_id=tenant.id,
        customer=customer,
        severity=RiskSeverity(body.severity),
        source=RiskSignalSource.manual,
        status=RiskSignalStatus.published,
        evidence={"note": body.note} if body.note else {},
    )
    await audit.record(
        db, action="risk.manual_report", tenant_id=tenant.id,
        actor_user_id=membership.user_id, entity_type="customer_risk_signal",
        entity_id=str(sig.id) if sig else str(body.customer_id),
        after={"severity": body.severity, "customer_id": str(body.customer_id)},
    )
    await db.commit()
    return {
        "ok": True,
        "created": sig is not None,
        "signal_id": str(sig.id) if sig else None,
    }
