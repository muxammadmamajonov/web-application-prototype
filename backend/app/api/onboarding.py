"""Public onboarding metadata — the business-creation wizard reads its option
lists (business types + acquisition channels) from here. Editable by HQ via the
platform settings store; falls back to curated defaults."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.services.taxonomy import get_business_types, get_how_found_options

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.get("/options")
async def onboarding_options(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Option lists for the business-creation wizard. No auth — the wizard runs
    before a tenant exists and the data is non-sensitive."""
    return {
        "business_types": await get_business_types(db),
        "how_found_options": await get_how_found_options(db),
    }
