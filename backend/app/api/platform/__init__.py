"""Platform (HQ) console API.

One module per domain, registered here in the order the handlers were declared
when they lived in a single file — so the mounted URL surface, and the sequence
FastAPI resolves it in, are exactly what they were. Each module owns a
contiguous run of that original sequence, which is what lets seven files
reproduce it without reordering anything.

The names re-exported at the bottom are the ones tests already import from
``app.api.platform``; they keep resolving after the split.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.dbauth import platform_authority

from . import (
    broadcasts,
    campaigns,
    directory,
    overview,
    provisioning,
    referrals,
    tenants,
)
from .provisioning import (  # noqa: F401  — re-exported for existing importers
    PlanCreateIn,
    PlanOverridesIn,
    PlanPatchIn,
)
from .tenants import (  # noqa: F401  — re-exported for existing importers
    TenantIn,
    create_tenant,
)

#: Every HQ route inherits the platform DB authority here rather than in the
#: handler, so an endpoint added to this package cannot be shipped without it
#: — and an endpoint added anywhere else cannot acquire it by accident.
router = APIRouter(
    prefix="/platform", tags=["platform"],
    dependencies=[Depends(platform_authority)],
)
router.include_router(broadcasts.router)
router.include_router(overview.router)
router.include_router(tenants.router)
router.include_router(provisioning.router)
router.include_router(directory.router)
router.include_router(campaigns.router)
router.include_router(referrals.router)
