"""Resolving a Telegram deep-link intent to a screen the caller may see.

INVARIANT: two questions, in order, never merged — what does this intent point
at (signature only), then may THIS caller go there (session authorization, as
if the intent did not exist). Forwarding the link therefore grants nothing.

INVARIANT: the response is a route, never data. One `unknown` answer covers
forged, expired, missing and not-yours, so this is not an oracle for which
payment ids exist.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.review_intent import (
    MAX_STARTAPP_LEN,
    PURPOSE_PAYMENT_REVIEW,
    InvalidIntent,
    resolve,
)
from app.core.security import current_user
from app.db import get_db
from app.models import Payment, TenantUser, User

log = logging.getLogger("muddato.review_intent")
router = APIRouter(tags=["review-intents"])


class IntentIn(BaseModel):
    intent: str = Field(max_length=MAX_STARTAPP_LEN)


class IntentOut(BaseModel):
    """Where to go, or that there is nowhere to go.

    ``unknown`` is returned for an intent that is malformed, expired, signed by
    somebody else, points at a row that is gone, or points at a row this caller
    may not see. One answer for all of them on purpose: distinguishing them
    turns this endpoint into an oracle for whether a payment id exists and who
    it belongs to.
    """

    status: str                      # "ok" | "unknown"
    route: str | None = None
    tenant_id: str | None = None


@router.post("/review-intents/resolve", response_model=IntentOut)
async def resolve_review_intent(
    body: IntentIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> IntentOut:
    """Resolve a deep-link intent for the SIGNED-IN caller.

    Authentication is a dependency rather than a check inside the body, so an
    anonymous caller never reaches the resolution logic at all.
    """
    try:
        purpose, object_id = resolve(body.intent)
    except InvalidIntent:
        return IntentOut(status="unknown")

    if purpose != PURPOSE_PAYMENT_REVIEW:      # pragma: no cover — closed set
        return IntentOut(status="unknown")

    # (Batch 5) The signed intent names one payment. Narrow to that payment's
    # tenant before reading it; the owner/manager check below is unchanged and
    # still decides whether this caller may act on it.
    from app.core.dbauth import scope_to_object

    await scope_to_object(db, "payment", object_id)
    payment = (await db.execute(
        select(Payment).where(Payment.id == object_id)
    )).scalar_one_or_none()
    if payment is None:
        return IntentOut(status="unknown")

    # The intent named a payment; it did NOT say the caller may review it. This
    # is the same owner/manager rule the review and approve endpoints apply —
    # sales staff take payments, they do not adjudicate them.
    membership = (await db.execute(
        select(TenantUser).where(
            TenantUser.user_id == user.id,
            TenantUser.tenant_id == payment.tenant_id,
            TenantUser.is_active.is_(True),
            TenantUser.role.in_(["owner", "manager"]),
        )
    )).scalar_one_or_none()
    if membership is None:
        # Logged because a forwarded link being opened by the wrong person is
        # worth seeing in aggregate. No ids beyond the actor: the point is the
        # rate, not the target.
        log.info("review_intent.denied user=%s", user.id)
        return IntentOut(status="unknown")

    return IntentOut(
        status="ok",
        route=f"/tenant/payments/{payment.id}/review",
        tenant_id=str(payment.tenant_id),
    )
