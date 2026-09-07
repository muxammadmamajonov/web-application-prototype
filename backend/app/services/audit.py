"""Audit-log helper.

A single ``record()`` call from a route handler appends a row to the
``audit_log`` table without committing — the caller's transaction will
flush it. This keeps the audit write inside the same unit-of-work as
the action it describes (so we don't get phantom audit rows for
operations that ultimately rolled back).

Round 18+'s daily cron will reuse this to log overdue marks, late-fee
applications and reminder sends.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

# Only *significant* events are persisted — platform admin actions, money /
# subscription changes, access grants/revocations and destructive operations.
# High-frequency routine ops (every customer approval, payment approval,
# invitation, broadcast, edit) are intentionally NOT audited to keep the log
# small and forensically useful. Edit this set to change what's recorded.
AUDITED_ACTIONS: frozenset[str] = frozenset({
    # platform / HQ
    "tenant.create", "tenant.delete", "tenant.balance_topup", "tenant.set_plan",
    "tenant.status_change", "tenant.plan_change",
    "admin_invitation.create", "admin_invitation.claim", "admin.update", "admin.revoke",
    "plan.create", "plan.update", "plan.delete",
    # privileged impersonation: a platform super-admin entering a tenant's
    # dashboard they are not a member of (#109) — must always leave a trace.
    "tenant.super_admin_enter",
    "tenant.impersonated_mutation",   # (#736) every impersonated mutating request
    # money settlement (provider webhooks) + subscription
    "subscription.payment", "subscription.confirm",
    # access removal + destructive tenant ops
    "staff.revoke", "installment.cancel",
    "staff.update",   # (MUDD-137) role change or activate/deactivate short of revoke
    # data-subject erasure (#167) — irreversible PII removal must always leave a trace
    "customer.erased",
    "customer.erasure_requested",   # (#248) customer self-service erasure request (notifies the shop)
    # (#129) These all had audit.record() calls but were missing from the allowlist,
    # so the rows were silently dropped. Now persisted:
    "customer.approve", "customer.reject",                           # KYC decisions
    "payment.approve", "payment.reject", "payment.record_approved",  # payment lifecycle
    "installment.edit", "installment.reschedule",                    # contract edits
    "staff.invite", "invitation.create", "invitation.claim",        # access grants
    "tenant.bot_activate",                                           # tenant config
    # (MUDD-137) Pricing policy + payment-provider config changed via
    # PATCH /tenant/settings — commission/late-fee/markup %, click/payme
    # merchant ids and secret-key presence (never the secret value itself).
    "tenant_settings.update",
    "broadcast.send", "broadcast.delete",                           # mass comms
    "risk.manual_report",                                            # risk network
    # (MUDD-136) a reminder that reached NO channel at all (no linked
    # Telegram, no usable SMS fallback) or reached a channel but every
    # bounded-retried attempt permanently failed — previously invisible
    # beyond a Fly worker log line. `GET /platform/audit` is the named
    # operator surface for requirement 7's "recorded and visible to an admin".
    "reminder.delivery_failed",
})


# (#127) Request-scoped impersonator. When a platform super-admin enters a tenant,
# resolve_tenant stamps their id here so every audit row written during the request
# is attributed to the impersonating admin — not just the super_admin_enter row.
# Task-local (set per request), mirroring the tenant log binding.
_impersonator: ContextVar[UUID | None] = ContextVar("audit_impersonator", default=None)


def set_impersonator(user_id: UUID | None) -> None:
    """Stamp (or clear) the impersonating super-admin for the current request."""
    _impersonator.set(user_id)


async def record(
    db: AsyncSession,
    *,
    action: str,
    tenant_id: UUID | None = None,
    actor_user_id: UUID | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Append an audit row to ``db``'s pending unit-of-work.

    ``action`` is a short verb-noun like ``"tenant.create"``. Only actions in
    ``AUDITED_ACTIONS`` are persisted; everything else is silently dropped so
    the log stays small and meaningful.
    """
    if action not in AUDITED_ACTIONS:
        return
    # (#127) Stamp the impersonating super-admin (if any) into the after-state, so
    # the mutation is traceable to them — not just the super_admin_enter row.
    imp = _impersonator.get()
    if imp is not None:
        after = {**(after or {}), "_impersonated_by": str(imp)}
    db.add(AuditLog(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        before_json=before,
        after_json=after,
        ip=ip,
        user_agent=user_agent,
    ))


__all__ = ("record", "set_impersonator")
