"""Who may mint which invitation.

An invitation is a **bearer credential**. Whoever holds the link can bind a
Telegram account to a customer record, or take a seat in the back office with
the role the invite carries. That makes minting an authorization decision, and
until Batch 6 it was three different decisions in three places — one of which was
no decision at all:

* ``POST /tenant/invitations`` checked owner-or-manager and refused a manager
  minting an owner seat. Correct, and the only place that had a rule.
* ``POST /tenant/customers`` and ``POST /tenant/customers/{id}/invite`` both mint
  a customer invitation and checked **nothing**. Any member of the tenant,
  including ``sales``, could mint a live bearer credential for any customer in
  the shop, and nothing recorded that they had.
* The platform paths check ``_require_super``, which is right but was written
  separately again.

This module is the single table. Absence from it is denial, so a new invitation
kind is refused until somebody writes down who may create it — the opposite of
the previous default, where a new mint site inherited whatever guard its handler
happened to have.

WHY ``sales`` KEEPS THE CUSTOMER INVITE. It is not an oversight carried forward.
Sales staff are the intake role: they create customers, contracts, guarantors and
payments (they cannot approve payments, cancel contracts, promote a pending
customer, or touch the catalogue). A customer invitation binds one Telegram
account to one customer record **in their own shop**, which is the last step of
the intake they are already trusted to perform. Denying it would break the
walk-in flow without denying them anything they cannot already reach through the
dashboard.

The residual risk is a bearer credential leaving the shop deliberately — a sales
employee minting an invite for a customer and forwarding it. RLS cannot help
there (the row is genuinely theirs to see) and neither can a role check. What
closes it is attribution: every mint now records ``created_by_user_id`` and
writes an audit row, so the question "who issued this link" has an answer.
"""
from __future__ import annotations

from enum import StrEnum

from fastapi import HTTPException, status

from app.models.enums import TenantRole


class MintSurface(StrEnum):
    """WHERE the mint was requested. The kind alone is not enough.

    The same ``customer`` invitation is a different act depending on the route
    that produced it, and the product already treated them differently:

    * ``INTAKE`` — ``POST /tenant/customers`` and ``…/{id}/invite``. One invite,
      for one customer being served at the desk right now. Part of the job sales
      staff already do.
    * ``CONSOLE`` — ``POST /tenant/invitations``. The back-office credential
      screen: mint any kind, list them, revoke them. Owner/manager only, and it
      was already enforced that way — ``test_sales_cannot_mint_or_revoke``
      exists precisely so a refactor cannot quietly reopen it.

    Collapsing the two into one per-kind table reopens the console for sales.
    That is not hypothetical: the first draft of this module did exactly that and
    the route-contract test caught it.
    """

    intake = "intake"
    console = "console"


#: (surface, kind) → the tenant roles that may mint it. **An unlisted pair is
#: denied**, which is what makes this an allow-list rather than a suggestion.
TENANT_MINT_ALLOWED: dict[tuple[MintSurface, str], frozenset[TenantRole]] = {
    # Intake: one credential for the customer in front of you.
    (MintSurface.intake, "customer"):
        frozenset({TenantRole.owner, TenantRole.manager, TenantRole.sales}),
    (MintSurface.intake, "guarantor"):
        frozenset({TenantRole.owner, TenantRole.manager, TenantRole.sales}),
    # Console: managing credentials in bulk is a back-office act.
    (MintSurface.console, "customer"):
        frozenset({TenantRole.owner, TenantRole.manager}),
    (MintSurface.console, "guarantor"):
        frozenset({TenantRole.owner, TenantRole.manager}),
    (MintSurface.console, "staff"):
        frozenset({TenantRole.owner, TenantRole.manager}),
}

#: Kinds that are never mintable from a tenant session at all — they carry
#: platform authority and are minted by HQ behind ``_require_super``.
PLATFORM_ONLY_KINDS: frozenset[str] = frozenset({"platform_admin"})

#: The ceiling on the authority an invitation may CARRY, independent of who may
#: mint that kind at all. A manager may mint staff invitations; a manager may not
#: mint an OWNER seat, for themselves or for a new hire. Invitations are the only
#: staff-creation path, so this is the sole ceiling on privilege escalation
#: inside a tenant.
GRANTED_ROLE_CEILING: dict[str, frozenset[TenantRole]] = {
    "owner": frozenset({TenantRole.owner}),
}


def require_may_mint(
    membership: object, kind: str, *, surface: MintSurface,
    granted_role: str | None = None,
) -> None:
    """Refuse unless ``membership`` may mint ``kind`` on ``surface`` carrying ``granted_role``.

    Raises 403 and returns nothing — the caller proceeds only if it was allowed.
    Deliberately not a boolean: a helper that returns False is a helper whose
    result can be ignored, and this is the only thing standing between a sales
    account and a back-office seat.
    """
    role = getattr(membership, "role", None)

    if kind in PLATFORM_ONLY_KINDS:
        # Reachable only if a tenant route ever accepts this kind as input.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invite_kind_not_tenant_mintable")

    allowed = TENANT_MINT_ALLOWED.get((surface, kind))
    if allowed is None:
        # A kind nobody has classified. Denied on purpose: this is the branch
        # that makes adding a mint site without a policy fail loudly.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invite_kind_not_permitted")
    if role not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invite_role_not_permitted")

    ceiling = GRANTED_ROLE_CEILING.get(granted_role or "")
    if ceiling is not None and role not in ceiling:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden_role_escalation")


__all__ = (
    "GRANTED_ROLE_CEILING",
    "PLATFORM_ONLY_KINDS",
    "TENANT_MINT_ALLOWED",
    "MintSurface",
    "require_may_mint",
)
