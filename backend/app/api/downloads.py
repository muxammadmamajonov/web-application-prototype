"""Short-lived signed download URLs (#10).

THE PROBLEM. Contract PDFs, receipts, invoices and the GDPR export are all
generated behind a bearer token. A Telegram WebView cannot attach that token to
a plain link, so every one of these downloads was fetched with the token into a
``blob:`` URL and then handed to ``Telegram.WebApp.openLink`` — which refuses a
``blob:`` origin. The result was a button that did nothing at all, silently, for
the platform's primary audience. Desktop browsers fared better but still got
``<a download>`` behaviour that some engines ignore.

THE SHAPE OF THE FIX. Two endpoints:

  ``POST /downloads/grant``  authenticated. Performs exactly the authorization
                             check the direct endpoint performs, then signs
                             ``kind:ident:exp`` and returns an ABSOLUTE https URL.
  ``GET  /dl/{kind}/{ident}`` unauthenticated. Verifies the signature and expiry,
                             then renders. The signature IS the capability.

The authorization logic is not duplicated: the grant is the only place a
permission is evaluated, and a valid signature is proof that an authorized user
asked for this object inside the TTL. That is also why the TTL is short — the
link is handed out per click, not stored, so it only has to survive the moment
between the click and the browser opening it.

WHY NOT REUSE ``sign_receipt``. Those are deliberately permanent: they are
embedded once into a stored ``receipt_image_url`` and must keep verifying
forever. These are the opposite — ephemeral, minted per click — so they get
their own keyspace and their own expiry rather than loosening a mechanism whose
existing signatures must stay stable.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.ratelimit import pdf_limit
from app.core.security import current_user, sign_download, verify_download_sig
from app.db import get_db
from app.models import Payment, User

log = logging.getLogger(__name__)
router = APIRouter(tags=["downloads"])

# Long enough for a slow phone to hand the URL to a system browser that then
# cold-starts and re-requests it; short enough that a leaked link is worthless
# by the time it is pasted anywhere.
DOWNLOAD_TTL = timedelta(minutes=10)

DownloadKind = Literal["contract", "payment_receipt", "invoice"]

#: (MUDD-139) The wire kind for a contract whose identity-document fields must be
#: masked. It is a distinct kind rather than a query parameter on purpose: `kind`
#: is INSIDE the signed payload (`sign_download`), so a `sales` member holding a
#: masked link cannot edit it into the unmasked one — they would need the signing
#: key. `/api/dl/...` has no authenticated user, so the visibility decision has to
#: travel in something the caller cannot forge.
CONTRACT_MASKED = "contract_masked"


def _base_kind(kind: str) -> str:
    """The object kind behind a wire kind — everything except visibility.

    Tenant scoping, the RLS owner lookup and the human-readable filename are all
    properties of the OBJECT, so they must not see the masked variant as an
    unknown kind (which would silently return None / no scope).
    """
    return "contract" if kind == CONTRACT_MASKED else kind


def _public_base() -> str:
    """The origin a Telegram client should be sent to.

    ``frontend_url`` and not the API host: the frontend proxies ``/api`` to the
    backend, so a same-origin URL works in the browser AND avoids a second
    CORS/cookie domain. Falling back to a relative path would defeat the point —
    ``openLink`` needs an absolute https URL.
    """
    return settings.frontend_url.rstrip("/")


def build_download_url(kind: str, ident: str) -> tuple[str, int]:
    """Mint an absolute, signed, expiring URL. Returns ``(url, expires_at)``."""
    exp = int((datetime.now(UTC) + DOWNLOAD_TTL).timestamp())
    sig = sign_download(kind, ident, exp)
    return f"{_public_base()}/api/dl/{kind}/{ident}?e={exp}&s={sig}", exp


class GrantIn(BaseModel):
    kind: DownloadKind
    id: str
    # "telegram" delivers the file into the caller's own chat instead of
    # returning a URL. The client asks for it when it is running inside the
    # Mini App, where a chat message is a better artefact than a browser tab:
    # it persists, it is searchable, and it survives the app closing.
    deliver: Literal["url", "telegram"] = "url"


class GrantOut(BaseModel):
    # Exactly one of these is set. `url` for deliver="url"; `sent` for
    # deliver="telegram", so the client can say so rather than guess.
    url: str | None = None
    expires_at: int | None = None
    sent: bool = False
    #: Which delivery outcome produced `sent` — SENT, ALREADY_SENT or
    #: IN_PROGRESS. The client renders one state for all three (the document is
    #: or will be in the chat) but the distinction is worth surfacing rather
    #: than flattening it away before anyone can see it.
    state: str | None = None


async def _owning_tenant(db: AsyncSession, *, kind: str, obj_id: UUID) -> UUID | None:
    """The shop a document belongs to — its archive topic and its isolation key."""
    from app.core.dbauth import scope_to_object
    from app.models import Installment, Invoice

    # (Batch 5) Narrow an unscoped capability request to this object's tenant
    # before reading it. No-op when the caller already carries a scope.
    kind = _base_kind(kind)
    await scope_to_object(db, kind, obj_id)

    if kind == "contract":
        return (await db.execute(
            select(Installment.tenant_id).where(Installment.id == obj_id)
        )).scalar_one_or_none()
    if kind == "payment_receipt":
        return (await db.execute(
            select(Payment.tenant_id).where(Payment.id == obj_id)
        )).scalar_one_or_none()
    if kind == "invoice":
        return (await db.execute(
            select(Invoice.tenant_id).where(Invoice.id == obj_id)
        )).scalar_one_or_none()
    return None


async def _render(
    *, kind: str, obj_id: UUID, db: AsyncSession,
) -> tuple[bytes, str, str]:
    """Produce one download's ``(bytes, filename, mime)``.

    Shared by both delivery paths on purpose: the signed URL and the Telegram
    hand-off must produce byte-identical documents, and duplicating the switch
    is how they would quietly stop doing so.
    """
    from app.core.documents import document_filename
    from app.services.document_delivery import document_reference

    # The same name on both delivery routes. A UUID filename is unreadable in a
    # chat list and unsearchable in a downloads folder; the contract number is
    # the identifier both the shop and the customer already quote.
    base = _base_kind(kind)
    name = document_filename(base, reference=await document_reference(db, base, obj_id))

    if base == "contract":
        from app.services.pdf import render_contract_pdf
        # (MUDD-139) The masked variant is a different DOCUMENT, not a different
        # permission check — the check already ran at grant time, and its answer
        # is what the signed kind carries.
        return (
            await render_contract_pdf(db, obj_id, mask_pii=(kind == CONTRACT_MASKED)),
            name, "application/pdf",
        )
    if kind == "payment_receipt":
        from app.services.pdf import render_receipt_pdf
        return await render_receipt_pdf(db, obj_id), name, "application/pdf"
    if kind == "invoice":
        from app.services.pdf import render_invoice_pdf
        return await render_invoice_pdf(db, obj_id), name, "application/pdf"
    # Unreachable through the signed route (kind is inside the signed payload),
    # so this can only be a signature minted for a kind since removed.
    raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown_kind")


@router.post("/downloads/grant", response_model=GrantOut)
async def grant_download(
    body: GrantIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> GrantOut:
    """Authorize the caller for one object, then hand back a signed URL.

    Every branch delegates to the SAME access check the direct endpoint uses, so
    there is one definition of who may read a contract. Adding a kind here
    without an access check would mint a capability for anyone — hence the
    explicit ``else: raise`` rather than a permissive default.
    """
    from app.api.pdf import _can_access_installment
    from app.core.dbauth import scope_to_object

    try:
        ident = str(UUID(body.id))          # reject anything not a UUID outright
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_id") from None

    # (Batch 5) The caller named one object. Narrow the request to that object's
    # tenant before any of the access checks below read a row: the checks decide
    # WHETHER the caller may have it, this decides how much the database would be
    # willing to show if one of them were ever forgotten.
    await scope_to_object(db, body.kind, ident)

    # The wire kind equals the requested kind unless the caller must receive a
    # masked contract (MUDD-139); set inside the `contract` branch below.
    wire_kind: str = body.kind

    if body.kind == "contract":
        await _can_access_installment(db, user=user, installment_id=UUID(ident))
        from app.core.pii_policy import contract_pii_masked_for

        if await contract_pii_masked_for(db, user=user, installment_id=UUID(ident)):
            wire_kind = CONTRACT_MASKED
    elif body.kind == "payment_receipt":
        pay = (await db.execute(
            select(Payment).where(Payment.id == UUID(ident))
        )).scalar_one_or_none()
        if pay is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "payment_not_found")
        await _can_access_installment(db, user=user, installment_id=pay.installment_id)
    elif body.kind == "invoice":
        # Invoices are tenant-scoped and owner/manager-only on the direct route.
        # Resolving the membership here would duplicate `resolve_tenant`'s header
        # handling, so the invoice is matched against the caller's tenants.
        from app.models import Invoice, TenantUser

        inv = (await db.execute(
            select(Invoice).where(Invoice.id == UUID(ident))
        )).scalar_one_or_none()
        if inv is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "invoice_not_found")
        allowed = (await db.execute(
            select(TenantUser.id).where(
                TenantUser.user_id == user.id,
                TenantUser.tenant_id == inv.tenant_id,
                TenantUser.is_active.is_(True),
                TenantUser.role.in_(["owner", "manager"]),
            )
        )).first()
        if not allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    else:                                    # pragma: no cover — Literal-guarded
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_kind")

    if body.deliver == "telegram":
        # Deliberately NO url in the response: inside the Mini App a link is the
        # thing that does not work, and returning one anyway would leave a live
        # capability lying in the client for no reason.
        #
        # Delivery is idempotent and rate-limited in `document_delivery`, and
        # authoritative there rather than here: a client that never sees our
        # response will tap again, and a client can be bypassed entirely.
        from app.services.document_delivery import DeliveryState, deliver_to_user

        obj_id = UUID(ident)
        tenant_id = await _owning_tenant(db, kind=body.kind, obj_id=obj_id)

        async def _render_bytes() -> bytes:
            # `wire_kind`, not `body.kind`: the Mini-App hand-off must deliver the
            # same document the signed URL would, masked included.
            data, _name, _mime = await _render(kind=wire_kind, obj_id=obj_id, db=db)
            return data

        result = await deliver_to_user(
            db, user=user, kind=body.kind, object_id=obj_id,
            tenant_id=tenant_id, render=_render_bytes,
            locale=(user.locale or "uz"),
        )
        await db.commit()

        if result.state is DeliveryState.rate_limited:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too_many_documents")
        if result.state is DeliveryState.unavailable:
            # Not an error — a normal outcome the client can act on (the person
            # has never opened a chat with the bot). A 200 with `state` set lets
            # the client tell this apart from a genuine send failure and offer
            # "open the bot" instead of a bare retry; collapsing it into the
            # same 502 as `failed` below is what left the frontend with nothing
            # to distinguish them by.
            return GrantOut(sent=False, state=result.state.value)
        if result.state is DeliveryState.failed:
            # A genuine, likely-transient Telegram send failure. The client
            # falls back to its own download path, so say what happened
            # without pretending it succeeded.
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, "telegram_delivery_unavailable",
            )
        # `already_sent` and `in_progress` both mean "do not send another".
        # The client shows "sent", which is what the user's chat actually shows.
        return GrantOut(sent=True, state=result.state.value)

    url, exp = build_download_url(wire_kind, ident)
    return GrantOut(url=url, expires_at=exp)


@router.get("/dl/{kind}/{ident}", dependencies=[pdf_limit()])
async def serve_download(
    kind: str,
    ident: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    e: Annotated[int, Query(description="signed expiry, unix seconds")] = 0,
    s: Annotated[str | None, Query(description="signature")] = None,
) -> Response:
    """Serve a previously granted download. No session required — see module doc.

    One 403 for every failure mode (bad signature, tampered expiry, elapsed
    deadline). Distinguishing them would tell a prober which part of a guess was
    right, and none of the distinctions help a legitimate caller, whose link is
    either fresh or is re-minted by clicking again.
    """
    if not verify_download_sig(kind, ident, e, s):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "bad_or_expired_signature")

    try:
        obj_id = UUID(ident)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_id") from None

    # (Batch 5) The signature is the authorization; this is the reach. A valid
    # signature over one object now scopes the render to that object's tenant
    # instead of running unscoped, which under the old policies meant every
    # tenant's rows were readable for the duration of the render.
    from app.core.dbauth import scope_to_object

    # (MUDD-139) `_base_kind`, not `kind`: the masked contract is a visibility
    # variant of the SAME object, and scoping is a property of the object. Passing
    # the wire kind here leaves `scope_to_object` with a name it does not know, so
    # the render runs unscoped — under RLS that means the row is filtered away and
    # the download 500s. Fail-closed, but a salesperson's contract would never open.
    await scope_to_object(db, _base_kind(kind), obj_id)

    from app.api.media import _content_disposition

    data, name, mime = await _render(kind=kind, obj_id=obj_id, db=db)

    return Response(
        content=data,
        media_type=mime,
        headers={
            # `attachment`, not `inline`: this URL exists so the file SAVES.
            # Built by media.py's RFC 6266 + RFC 5987 helper rather than an
            # f-string, which is what stops a non-ASCII filename becoming a 500.
            "Content-Disposition": _content_disposition("attachment", name),
            "Cache-Control": "private, no-store",
        },
    )
