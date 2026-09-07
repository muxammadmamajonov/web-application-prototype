"""Who may read a customer's identity documents — one definition, every surface.

MUDD-55 established the rule: inside a shop, ``passport_*``, ``jshshir``,
``birthdate`` and ``address`` are owner/manager data. A ``sales`` member gets the
customer record with those fields replaced by ``***`` (``_customer_dict`` /
``_guarantor_dict``) and is refused the raw ID scan behind them
(``/customers/{cid}/kyc-media``).

MUDD-139 found the rule had exactly one hole left, and it was the widest one: the
**contract PDF**. ``_can_access_installment`` admits any active member of the
owning tenant with no role check, and the contract template prints the customer's
passport series/number, JSHSHIR, birthdate and home address verbatim — so a
salesperson who is shown ``***`` on the customer page reads the real values by
opening the contract for that same customer, one contract at a time, for every
customer in the shop. ``POST /downloads/grant`` widened it further: it mints a
10-minute *unauthenticated* URL for the same document.

This module is the single place that answers "must this caller's copy be
masked?", so the customer page, the contract PDF and the signed download can no
longer drift apart the way they had.

The answer is deliberately NOT "refuse". MUDD-55's own precedent is to hand a
``sales`` member the record with the sensitive fields blanked, not a 403 — the
salesperson still needs the contract's items, amounts and schedule to do the sale.

Whether a salesperson should additionally be able to print a *signature-ready*
(unmasked) contract is a business call, not a security one. It is **open**, as
entry #18 on MUDD-94's decision queue (posted 2026-09-06; an earlier revision of
this docstring asserted it had been raised when it had not — the reviewer caught
that, and this line now says what is actually true). Until it is answered this
fails safe: masked. Answer A leaves this module as it stands; answer B adds an
owner/manager-countersigned print path on top of it, so neither answer reverts
what is here.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Installment, TenantUser, User
from app.models.enums import TenantRole

#: The roles MUDD-55 trusts with a customer's identity documents.
PII_VISIBLE_ROLES: tuple[TenantRole, ...] = (TenantRole.owner, TenantRole.manager)

#: The sentinel ``_customer_dict``/``_guarantor_dict`` already write. Kept
#: byte-identical so a masked contract and a masked customer page say the same
#: thing, and so ``_is_masked_pii`` keeps recognising it if one is ever fed back.
PII_MASK = "***"


def role_sees_full_pii(role: TenantRole | str | None) -> bool:
    """True when ``role`` may read passport / JSHSHIR / birthdate / address."""
    if role is None:
        return False
    try:
        return TenantRole(role) in PII_VISIBLE_ROLES
    except ValueError:
        # An unknown role string is not a known-trusted one. Deny.
        return False


def mask_pii_fields(block: dict | None, fields: tuple[str, ...]) -> dict | None:
    """Return ``block`` with ``fields`` replaced by :data:`PII_MASK`.

    A field that is absent or already empty stays empty rather than becoming
    ``***``: a mask over nothing would tell the reader a value exists when none
    does, which is the same existence oracle the masking exists to avoid.
    """
    if not block:
        return block
    out = dict(block)
    for name in fields:
        if out.get(name):
            out[name] = PII_MASK
    return out


#: The contract-template keys that carry identity-document data.
CONTRACT_PII_FIELDS: tuple[str, ...] = (
    "passport_id", "passport_series", "passport_number",
    "jshshir", "birthdate", "address",
)

#: The guarantor block's keys. A guarantor is the same PII class as the customer
#: (MUDD-55 H2 #1), but the guarantor record carries one field the customer block
#: on a contract does not: ``photo_urls``, the scanned ID images. It is listed
#: here rather than reusing :data:`CONTRACT_PII_FIELDS` because the guarantor rows
#: are empty until Round 22 loads them — the moment the omission would otherwise
#: become a live leak is the moment nobody is looking at this file.
#:
#: NOT-YET-UNIFIED (MUDD-139 review, M-1): ``api/tenant/customers.py`` still keeps
#: its own ``_CUSTOMER_PII_FIELDS`` / ``_GUARANTOR_PII_FIELDS`` / ``_PII_MASK``.
#: That file belongs to the tenant-API lane, so this module cannot fold them in;
#: the hand-off is on MUDD-139 with a named owner. Until then, a field added to
#: one list and not the others is exactly the drift that produced this ticket's
#: P1 — check all three.
CONTRACT_GUARANTOR_PII_FIELDS: tuple[str, ...] = (
    *CONTRACT_PII_FIELDS, "photo_urls",
)


async def contract_pii_masked_for(
    db: AsyncSession, *, user: User, installment_id: UUID,
) -> bool:
    """Must THIS caller's copy of this contract have its PII masked?

    Three kinds of caller reach a contract (see ``api/pdf._can_access_installment``):

    * an **owner/manager** of the owning shop — no mask, they already read these
      fields on the customer page;
    * a **sales** member of the owning shop — masked, matching MUDD-55;
    * the **customer**, reading their own contract — no mask. Masking a person's
      own passport number on their own contract protects nobody and would hand
      them an unusable document.

    Anyone else never gets here: the access check 404s first. If this is somehow
    reached without an answer, it masks — the safe direction.
    """
    inst = (await db.execute(
        select(Installment.tenant_id, Installment.customer_id)
        .where(Installment.id == installment_id)
    )).first()
    if inst is None:
        return True
    tenant_id, customer_id = inst

    role = (await db.execute(
        select(TenantUser.role).where(
            TenantUser.tenant_id == tenant_id,
            TenantUser.user_id == user.id,
            TenantUser.is_active.is_(True),
        )
    )).scalar_one_or_none()
    if role is not None:
        return not role_sees_full_pii(role)

    # Not staff — then the access check let them through as the customer. Confirm
    # that here rather than assume it: this function is also called from the
    # signed-download grant, and "not a member" must not silently mean "trusted".
    from app.api.me import identity_is_provable
    from app.models import Customer

    if user.telegram_id is not None and identity_is_provable(user):
        owner_tg = (await db.execute(
            select(Customer.telegram_id).where(Customer.id == customer_id)
        )).scalar_one_or_none()
        if owner_tg is not None and owner_tg == user.telegram_id:
            return False
    return True
