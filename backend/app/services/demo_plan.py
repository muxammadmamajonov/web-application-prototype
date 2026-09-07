"""Demo (trial) plan configuration (Round 38).

The demo plan has no ``Plan`` row — it's the built-in "trial" tier. Its
behaviour (how long the trial lasts, what it's limited to, what happens when it
ends) is configured by the platform owner and stored in the ``demo_plan``
platform setting so it can be tuned without a deploy.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PlatformSetting

DEMO_PLAN_KEY = "demo_plan"

DEFAULTS: dict[str, Any] = {
    "trial_days": 14,
    "max_active_installments": None,   # None = unlimited
    "max_staff": 1,
    "max_customers": None,
    "on_expiry": "suspend",            # suspend | block | keep
}


async def get_config(db: AsyncSession) -> dict[str, Any]:
    """Merge stored overrides over DEFAULTS."""
    row = (await db.execute(
        select(PlatformSetting).where(PlatformSetting.key == DEMO_PLAN_KEY)
    )).scalar_one_or_none()
    cfg = dict(DEFAULTS)
    if row and isinstance(row.value, dict):
        for k in DEFAULTS:
            if (k in row.value and row.value[k] is not None) or k in row.value:
                cfg[k] = row.value[k]
    return cfg
