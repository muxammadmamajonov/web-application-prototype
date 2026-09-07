"""Demo request — a sales-led lead capture form.

A landing visitor fills in a short form; the lead goes to the Muddato support
channel and a human follows up. That is the whole flow.

It used to be self-serve: the form stashed the submission in Redis and handed
back a ``t.me/…?start=demo_<slug>`` deep link whose redemption provisioned a
Tenant and a trial subscription. That funnel (ONB-002) and the trial lifecycle
are classified LATER, so the redemption path and everything that fed it are gone
— see ``app/core/scope.py``. Git history holds the removed code.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, status
from pydantic import AliasChoices, BaseModel, Field

from app.core.ratelimit import public_form_limit
from app.core.validators import UzPhoneOrEmail
from app.services.support_channel import esc, send_to_support

router = APIRouter(prefix="/demo", tags=["demo"])


class DemoIntentRequest(BaseModel):
    business_name: str = Field(min_length=2, max_length=120)
    owner_name: str = Field(min_length=2, max_length=80)
    #: How to reach them — a UZ phone number or an email address. The form has
    #: always offered both (one `SmartContact` field, `isValidPhone ||
    #: isValidEmail`); it used to post them under the name `phone` against a
    #: phone-only type, so every email lead 422'd and was lost. `phone` stays
    #: readable as an input alias only, so a browser still holding the previous
    #: bundle does not drop a lead on the way to the next deploy; it accepts
    #: nothing the canonical name does not.
    contact: UzPhoneOrEmail = Field(
        min_length=4,
        max_length=128,
        validation_alias=AliasChoices("contact", "phone"),
    )
    # The plan the visitor was looking at when they asked — sales context, not a
    # selection. Kept loose so a catalog change never 422s a lead.
    plan_code: str = Field(default="", max_length=40)
    locale: str = Field(default="uz", pattern="^(uz|ru)$")
    referrer: str | None = Field(default=None, max_length=120)


@router.post("/intent", status_code=status.HTTP_202_ACCEPTED, dependencies=[public_form_limit()])
async def create_intent(body: DemoIntentRequest) -> dict:
    """Accept a demo request and hand it to the support channel for follow-up.

    Only the submitted fields travel — see ``app/services/support_channel``.
    """
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    text = (
        f"<b>🚀 Demo so'rovi</b>\n"
        f"\n"
        f"<b>Biznes:</b> {esc(body.business_name)}\n"
        f"<b>Ism:</b> {esc(body.owner_name)}\n"
        f"<b>Aloqa:</b> {esc(body.contact)}\n"
        f"<b>Tarif (qiziqish):</b> {esc(body.plan_code)}\n"
        f"<b>Til:</b> {esc(body.locale)}\n"
        f"<b>Manba:</b> {esc(body.referrer, limit=120)}\n"
        f"\n"
        f"<i>{ts}</i>"
    )
    await send_to_support(text)
    return {"ok": True}


__all__ = ("router",)
