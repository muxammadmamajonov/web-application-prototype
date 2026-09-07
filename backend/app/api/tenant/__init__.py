"""Tenant back-office API.

One module per domain, registered here in the order the handlers were declared
when they lived in a single file — so the mounted URL surface, and the sequence
FastAPI resolves it in, are exactly what they were. Each module owns a
contiguous run of that original sequence, which is what lets five files
reproduce it without reordering anything.

The names re-exported at the bottom are the ones other modules and tests already
import from ``app.api.tenant``; they keep resolving after the split.
"""
from __future__ import annotations

from fastapi import APIRouter

from . import analytics, billing, customers, imports, installments, payments
from ._shared import (  # noqa: F401  — re-exported for existing importers
    _MAX_ITEMS,
    _MAX_MONEY,
    _MAX_QTY,
    _MAX_UNIT_PRICE,
    DEFAULT_TENANT_TZ,
    CustomerIn,
    _bind_tenant_log,
    _require_owner_or_manager,
    _tenant_today,
    _tenant_tz_name,
    resolve_tenant,
)
from .billing import (  # noqa: F401  — re-exported for existing importers
    tenant_create_topup,
    tenant_plan_change,
)
from .customers import (  # noqa: F401  — re-exported for existing importers
    CustomerPatch,
    GuarantorIn,
    GuarantorPatch,
    RiskReportIn,
    _proxy_kyc,
    create_guarantor,
    kyc_media,
    list_customers,
)
from .imports import (  # noqa: F401  — re-exported for existing importers
    _apply_mapping,
    _parse_upload,
    create_broadcast,
)
from .installments import (  # noqa: F401  — re-exported for existing importers
    CancelIn,
    InstallmentIn,
    InstallmentItemIn,
    ProductPatch,
    _hydrate_from_catalog,
    cancel_installment,
    create_product,
)
from .payments import (  # noqa: F401  — re-exported for existing importers
    PaymentIn,
    create_payment,
)

router = APIRouter(prefix="/tenant", tags=["tenant"])
router.include_router(customers.router)
router.include_router(installments.router)
router.include_router(payments.router)
router.include_router(billing.router)
router.include_router(imports.router)
router.include_router(analytics.router)
