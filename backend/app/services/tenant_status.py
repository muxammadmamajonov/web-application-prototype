"""Tenant lifecycle status rules — what a locked shop may still do.

Muddato locks a shop by moving its :class:`~app.models.enums.TenantStatus` to
``suspended`` (read-only) or ``blocked`` (no access). MUDD-25 H1 made that lock
real: ``resolve_tenant`` refuses every mutating method for a locked tenant, so
hiding the UI is no longer the only thing standing between a locked shop and a
write.

The refusal was total, and that was the bug MUDD-54 R1 reports. The only two
self-service routes OUT of ``suspended``/``blocked`` are themselves POSTs, so
the guard sealed the exit along with the entrance: an expired trial could never
buy a plan and a shop behind on an invoice could never pay it. Recovery needed a
platform admin editing the row by hand.

Why a carve-out here is safe
----------------------------
``blocked``/``suspended`` are set in exactly two places, both automated and both
purely economic:

* ``services/billing.py`` ``run_dunning`` — invoices past due, and
* ``services/scheduler.py`` ``enforce_trials_job`` — the trial elapsed.

No operator endpoint sets either status by hand; there is no punitive, fraud or
abuse lock in this system. A locked shop is by construction a shop that owes
money, so "let it pay" cannot be a privilege escalation — it is the whole point
of locking it.

Why the rule stays in ``resolve_tenant`` (default-deny)
-------------------------------------------------------
The tempting alternative is to lift the guard out of ``resolve_tenant`` and hang
it on each *operational* route instead, leaving recovery routes bare. That
inverts the failure mode: every route added afterwards is unguarded until
somebody remembers the decorator, which is precisely the omission MUDD-25 H1
existed to close. So the guard stays where it is, refusing by default, and this
module names the narrow set of exceptions. A new route is locked unless a human
deliberately adds it below.

The allowlist is keyed on **whole routes**, never a path prefix. ``/billing/*``
would hand a locked tenant every present and future route under it —
``/billing/topups`` today, whatever an admin approval endpoint looks like
tomorrow.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.models.enums import TenantStatus

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import Tenant

#: Statuses in which a shop is locked out of ordinary writes.
LOCKED_STATUSES: frozenset[TenantStatus] = frozenset({
    TenantStatus.blocked,
    TenantStatus.suspended,
})

#: (#736) State-changing methods. An impersonated request using one of these must
#: always leave an audit trace (see ``resolve_tenant``), and a locked tenant is
#: refused one unless the route appears in ``RECOVERY_ROUTES``.
MUTATING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: The complete set of writes a LOCKED tenant may still make: paying money in,
#: and choosing the plan that payment buys. Both are matched as whole
#: ``(method, path)`` pairs against the route FastAPI resolved — never as a
#: prefix, and never against the raw request line.
#:
#: Adding an entry here grants it to every ``blocked`` and ``suspended`` tenant
#: in the platform. The bar is: does a shop that owes money need this route to
#: stop owing money? If not, it does not belong here.
RECOVERY_ROUTES: frozenset[tuple[str, str]] = frozenset({
    # Creates a pending top-up and returns the provider checkout URL. The
    # balance is credited by the provider webhook / an admin approving the
    # receipt, which settles open invoices and lifts a non-payment lock.
    ("POST", "/api/tenant/billing/topup"),
    # Selects a paid plan. This is the ONLY exit for a trial-expiry lock, where
    # nothing is owed and so no settlement can ever reactivate the shop.
    ("POST", "/api/tenant/plan-change"),
})


def is_recovery_route(method: str, path: str) -> bool:
    """Whether ``method path`` is one of the pay-your-way-back routes.

    Exact match on both halves. ``/api/tenant/billing/topup/approve`` and
    ``DELETE /api/tenant/billing/topup`` are not recovery routes.
    """
    return (method.upper(), path) in RECOVERY_ROUTES


def route_path(request: Request) -> str:
    """The path FastAPI matched, preferred over the one the client typed.

    ``scope["route"].path`` is the mounted route template — the router's own
    decision, already normalised for trailing slashes and redirects. Falling
    back to ``request.url.path`` is safe because the comparison is exact
    equality against a full absolute path, but the resolved route is the
    stronger signal and is what the allowlist is written against.
    """
    path = getattr(request.scope.get("route"), "path", None)
    return path if isinstance(path, str) else request.url.path


def may_mutate(tenant: Tenant, *, method: str, path: str) -> bool:
    """Whether ``tenant`` may perform ``method path``.

    Fail-closed for a locked tenant: ``True`` only for a read, or for one of the
    explicitly allowlisted recovery routes.
    """
    if method.upper() not in MUTATING_METHODS:
        return True
    if tenant.status not in LOCKED_STATUSES:
        return True
    return is_recovery_route(method, path)


async def may_self_clear_block(db: AsyncSession, tenant: Tenant) -> bool:
    """Whether a ``blocked`` tenant may lift its own block by buying a plan.

    (#MUDD-54 R1, second pass) The two rival fixes for this issue each answered
    this question with a constant, and each constant was wrong in one direction:

    * **Never lift.** ``settle_invoices_from_balance`` (``services/billing.py``)
      reactivates on ``invoices and not any_outstanding`` — it needs an invoice
      to settle. A trial-expiry block has none: ``enforce_trials_job`` walks
      ``demo → suspended → blocked`` (unconditionally, after
      ``SUSPEND_GRACE_DAYS``) for a shop that never owed anything, and
      ``tenant_plan_change`` charges the prepaid balance and writes its invoice
      already ``paid``. So a shop blocked by trial expiry — the DEFAULT path,
      not an exotic one — can top up, buy a plan, owe nothing, and stay blocked
      forever. That is the lockout MUDD-54 reported.
    * **Always lift.** ``PATCH /api/platform/tenants/{id}/status`` accepts
      ``blocked`` (``api/platform/provisioning.py``), so an operator CAN block a
      shop by hand — for fraud, for a legal hold, at a regulator's request.
      Lifting unconditionally lets the subject of that decision undo it by
      buying a plan, and lets a shop with overdue invoices shed the debt block
      the same way.

    ``blocked`` carries no reason column, so the reason has to be reconstructed:

    1. **Owes money → no.** An open/overdue invoice means this is a dunning
       block. The exit already exists and is the correct one: pay in, and
       ``settle_invoices_from_balance`` lifts the block when the money actually
       arrives. Buying a plan is not paying the invoice.
    2. **An operator set it → no.** ``tenant.status_change`` is written by
       exactly one caller, ``set_tenant_status`` — the platform-admin endpoint.
       ``run_dunning`` and ``enforce_trials_job`` write no audit row at all. So
       if the NEWEST ``tenant.status_change`` row for this tenant set
       ``blocked``, the lock is a human decision and its subject may not clear
       it. (An admin who later sets ``active`` leaves a newer row saying so, and
       a subsequent economic block is then correctly self-clearable.)
    3. Otherwise the block is economic and settled — lift it.

    KNOWN BOUND: ``purge_expired_pii`` drops ``audit_log`` rows older than
    ``_AUDIT_RETENTION_DAYS`` (365). An operator block that has stood for over a
    year loses its marker here and becomes self-clearable. Closing that needs a
    durable ``tenants.status_reason`` column rather than an inference from the
    log; tracked as follow-up, deliberately not smuggled into this fix.
    """
    from sqlalchemy import func
    from sqlalchemy import select as _select

    from app.models import AuditLog, Invoice
    from app.models.enums import InvoiceStatus

    if tenant.status is not TenantStatus.blocked:
        return True

    outstanding = await db.scalar(
        _select(func.count(Invoice.id)).where(
            Invoice.tenant_id == tenant.id,
            Invoice.status.in_((InvoiceStatus.open, InvoiceStatus.overdue)),
        )
    )
    if outstanding:
        return False

    latest_status_change = await db.scalar(
        _select(AuditLog.after_json)
        .where(
            AuditLog.tenant_id == tenant.id,
            AuditLog.action == "tenant.status_change",
        )
        .order_by(AuditLog.created_at.desc())
        .limit(1)
    )
    operator_blocked = (
        isinstance(latest_status_change, dict)
        and latest_status_change.get("status") == TenantStatus.blocked.value
    )
    return not operator_blocked
