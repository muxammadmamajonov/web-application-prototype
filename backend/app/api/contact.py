"""Public contact form — forwards submissions to the Muddato support channel."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ratelimit import public_form_limit
from app.db import get_db
from app.models import PlatformSetting
from app.services.support_channel import esc, send_to_support

router = APIRouter(prefix="/contact", tags=["contact"])

# What the app falls back to when HQ has not configured a channel. These are the
# values that were hardcoded into the client before this endpoint existed.
_SUPPORT_FALLBACK = {"support_username": "MuddatoSupport", "company_email": None}


@router.get("/channels")
async def support_channels(db: Annotated[AsyncSession, Depends(get_db)]) -> dict:
    """The support contacts the product should show users.

    These live in `platform_settings` under `support` and an operator edits them
    on Sozlamalar → Aloqa kanali — but until now nothing read them back. Every
    user-facing mention was a frontend constant, so the settings screen was a
    form whose values went nowhere: an operator could change the support handle
    and no user would ever see the new one, and the corporate email sat empty
    while the app displayed one anyway.

    Deliberately unauthenticated: a signed-out visitor who cannot get in is
    precisely who needs the support handle most. Only contact details are
    returned — never the internal Telegram channel id, which is an operational
    routing target, not a public one.
    """
    row = (
        await db.execute(select(PlatformSetting).where(PlatformSetting.key == "support"))
    ).scalar_one_or_none()
    stored = row.value if row and isinstance(row.value, dict) else {}

    username = str(stored.get("support_username") or "").strip().lstrip("@")
    email = str(stored.get("company_email") or "").strip()
    return {
        "support_username": username or _SUPPORT_FALLBACK["support_username"],
        # No fallback: an unset corporate address must read as absent rather than
        # advertise a mailbox nobody has been told to watch.
        "company_email": email or None,
    }


class ContactSubmission(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    contact: str = Field(min_length=4, max_length=128)
    contact_kind: str = Field(pattern="^(phone|email)$")
    message: str | None = Field(default=None, max_length=2000)
    locale: str = Field(default="uz", pattern="^(uz|ru)$")


@router.post("/submit", status_code=status.HTTP_202_ACCEPTED, dependencies=[public_form_limit()])
async def submit_contact(body: ContactSubmission) -> dict:
    """Format the form and post it into the Telegram support channel.

    Only the submitted fields travel — see ``app/services/support_channel``.
    """
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    text = (
        f"<b>📥 Yangi murojaat</b>\n"
        f"\n"
        f"<b>Sarlavha:</b> {esc(body.title)}\n"
        f"<b>{'Telefon' if body.contact_kind == 'phone' else 'E-pochta'}:</b> {esc(body.contact)}\n"
        f"<b>Til:</b> {esc(body.locale)}\n"
        f"\n"
        f"<b>Xabar:</b>\n{esc(body.message, limit=2000)}\n"
        f"\n"
        f"<i>{ts}</i>"
    )
    await send_to_support(text)
    return {"ok": True}
