"""Top-level API router — mounted by ``app.main`` under ``/api``."""
from __future__ import annotations

from fastapi import APIRouter

from . import (
    auth,
    auth_oidc,
    bot,
    catalog,
    contact,
    cron,
    demo,
    downloads,
    invitations,
    me,
    media,
    onboarding,
    pdf,
    plans,
    platform,
    receipts,
    reports,
    review_intents,
    sms,
    tenant,
    tenant_settings,
)
from .webhooks import click, payme, subscription

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(auth_oidc.router)
api_router.include_router(bot.router)
api_router.include_router(catalog.router)
api_router.include_router(contact.router)
api_router.include_router(cron.router)
api_router.include_router(demo.router)
api_router.include_router(invitations.router)
api_router.include_router(me.router)
api_router.include_router(downloads.router)
api_router.include_router(review_intents.router)
api_router.include_router(media.router)
api_router.include_router(onboarding.router)
api_router.include_router(pdf.router)
api_router.include_router(plans.router)
api_router.include_router(platform.router)
api_router.include_router(receipts.router)
api_router.include_router(reports.router)
api_router.include_router(sms.router)
api_router.include_router(tenant.router)
api_router.include_router(tenant_settings.router)
api_router.include_router(click.router, prefix="/webhooks")
api_router.include_router(payme.router, prefix="/webhooks")
api_router.include_router(subscription.router, prefix="/webhooks")


@api_router.get("/ping", tags=["meta"])
async def ping() -> dict[str, str]:
    return {"status": "pong"}
