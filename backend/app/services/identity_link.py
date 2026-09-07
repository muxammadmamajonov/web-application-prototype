"""Deciding which customer record a proved Telegram identity belongs to.

The founder's case is the whole problem in one line: a shop typed a customer's
number into its own books, the same person had already registered with the bot,
and nothing connected the two — so the app kept saying "Telegram not connected"
about somebody who plainly was.

Connecting them is a matching problem with several wrong answers available, so
this returns a STATE rather than performing a best guess. Exactly one candidate
links. Two candidates do not become a coin toss. A customer already bound to a
different account is not rebound. Everything else is somebody's job to resolve,
and the state says so.

TENANT SCOPE IS THE PRIVACY BOUNDARY. Every query here is filtered by the tenant
the caller is acting in, and "no match" is returned for both "nobody has that
number here" and "somebody in another shop does". The two must be
indistinguishable from outside, or this becomes a way to ask whether a phone
belongs to a Muddato customer anywhere.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.phone_identity import PhoneEligibility, mask
from app.models import Customer, User
from app.models.base import phone_identity
from app.models.enums import CustomerStatus

logger = logging.getLogger("muddato.identity")


class LinkState(StrEnum):
    """Every way the question can end. Rendered from a code, never from prose."""

    linked = "LINKED"
    #: Already bound to this very account — replays and reloads land here.
    already_linked = "ALREADY_LINKED"
    #: Nobody in THIS tenant holds that number. Also the answer when another
    #: tenant does: the caller must not be able to tell the difference.
    no_match = "NO_MATCH"
    #: More than one customer row in this tenant holds the number. A person
    #: cannot be two customers of one shop, and picking one would silently
    #: attach a stranger's contracts to this account.
    ambiguous = "RECONCILIATION_REQUIRED"
    #: The customer row is already bound to a different Telegram account.
    customer_taken = "CUSTOMER_LINKED_TO_OTHER_IDENTITY"
    #: This Telegram account is already the customer of this shop, as some
    #: other row.
    identity_taken = "IDENTITY_ALREADY_LINKED_HERE"
    #: Proved, but not a number this market serves.
    ineligible = "UNSUPPORTED_REGION"
    #: Nothing has been proved yet, so there is nothing to match on.
    unverified = "PHONE_NOT_VERIFIED"


#: States an operator has to act on, as opposed to states the person can fix.
NEEDS_OPERATOR: frozenset[LinkState] = frozenset({
    LinkState.ambiguous,
    LinkState.customer_taken,
    LinkState.identity_taken,
})


@dataclass(frozen=True, slots=True)
class LinkResult:
    state: LinkState
    customer_id: UUID | None = None
    #: How many rows matched. Bounded and non-identifying — it says "there is an
    #: ambiguity of this size", never who is in it.
    candidates: int = 0

    @property
    def ok(self) -> bool:
        return self.state in {LinkState.linked, LinkState.already_linked}


def _eligible(user: User) -> bool:
    return user.phone_eligibility == PhoneEligibility.eligible.value


async def find_candidates(
    db: AsyncSession, *, tenant_id: UUID, e164: str,
) -> list[Customer]:
    """Customer rows in ONE tenant whose number is the same subscriber.

    Compared through the same expression as `uq_customer_tenant_phone`, so a
    row stored as `+998 90 …` and a proof of `998…` match — otherwise a shop
    whose books predate canonicalisation could never be reconciled.
    """
    rows = (await db.execute(
        select(Customer).where(
            Customer.tenant_id == tenant_id,
            phone_identity(Customer.phone) == phone_identity(e164),
        ).order_by(Customer.created_at)
    )).scalars().all()
    return list(rows)


async def reconcile(
    db: AsyncSession,
    *,
    user: User,
    tenant_id: UUID,
    expected_customer_id: UUID | None = None,
) -> LinkResult:
    """Bind `user`'s proved identity to its customer row in `tenant_id`.

    `expected_customer_id` is the invitation's own binding. When present it is
    a CONSTRAINT, not a hint: the proved number must be that customer's, or the
    caller gets a mismatch rather than a link to whoever the number does match.

    Flushes, never commits — the caller keeps this in one transaction with the
    invitation being marked used.
    """
    if not user.verified_phone:
        return LinkResult(LinkState.unverified)
    if not _eligible(user):
        return LinkResult(LinkState.ineligible)

    # Already a customer of this shop? Idempotent for the row we would pick,
    # a refusal for any other.
    existing = (await db.execute(
        select(Customer).where(
            Customer.tenant_id == tenant_id, Customer.telegram_id == user.telegram_id,
        )
    )).scalars().all()
    if existing:
        bound = existing[0]
        if expected_customer_id is None or bound.id == expected_customer_id:
            return LinkResult(LinkState.already_linked, bound.id, len(existing))
        return LinkResult(LinkState.identity_taken, None, len(existing))

    candidates = await find_candidates(db, tenant_id=tenant_id, e164=user.verified_phone)

    if expected_customer_id is not None:
        # The invitation named a customer. The proof has to agree with it.
        target = next((c for c in candidates if c.id == expected_customer_id), None)
        if target is None:
            return LinkResult(LinkState.no_match, None, len(candidates))
        candidates = [target]

    if not candidates:
        return LinkResult(LinkState.no_match)
    if len(candidates) > 1:
        logger.warning(
            "identity.reconciliation_required tenant=%s phone=%s candidates=%d",
            tenant_id, mask(user.verified_phone), len(candidates),
        )
        return LinkResult(LinkState.ambiguous, None, len(candidates))

    target = candidates[0]
    if target.telegram_id is not None and target.telegram_id != user.telegram_id:
        return LinkResult(LinkState.customer_taken, None, 1)

    try:
        # THE SAVEPOINT OPENS FIRST, and the mutation happens inside it.
        #
        # `begin_nested()` flushes the session before it establishes the
        # savepoint. Assigning above this line therefore pushed the UPDATE out
        # ahead of the savepoint that was supposed to contain it, so a conflict
        # would have unwound to a point where the write had already happened —
        # and `session.rollback()`, the other obvious repair, unwinds the
        # OUTERMOST transaction: every other tenant's link and the phone proof
        # that started the sweep.
        async with db.begin_nested():
            target.telegram_id = user.telegram_id
            if target.status == CustomerStatus.pending:
                target.status = CustomerStatus.active
            await db.flush()
    except IntegrityError:
        # `uq_customer_tenant_telegram` — a concurrent claim bound this account
        # to another row between our read and our write. The database is the
        # only place that could have caught it.
        #
        # The savepoint is already released; the row's in-memory state is stale,
        # so it is expired and will be re-read rather than carrying the values
        # the database rejected.
        db.expire(target)
        logger.warning("identity.link_race tenant=%s user=%s", tenant_id, user.id)
        return LinkResult(LinkState.identity_taken)

    logger.info(
        "identity.linked tenant=%s user=%s customer=%s phone=%s",
        tenant_id, user.id, target.id, mask(user.verified_phone),
    )
    return LinkResult(LinkState.linked, target.id, 1)


# ── the orchestrator ────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class SweepOutcome:
    """What a whole-estate reconciliation did, counted rather than named.

    Deliberately not a list of tenants or customers: the caller is a bot reply
    and an audit line, and neither may enumerate the shops a person deals with.
    """

    linked: int = 0
    already: int = 0
    needs_operator: int = 0
    tenants_seen: int = 0

    @property
    def any_change(self) -> bool:
        return self.linked > 0


async def reconcile_all_tenants(db: AsyncSession, *, user: User) -> SweepOutcome:
    """Link a freshly proved identity to the customer records already waiting.

    This is the half the bot could not do on its own. A contact arrives in a
    private chat with no shop attached to it, so the handler knows WHO but not
    WHERE; meanwhile the merchant typed this person's number into their books
    days ago and the app has been calling them unconnected ever since.

    Runs inside the trusted backend, so it may look across tenants to find the
    candidates — but every DECISION is taken per tenant and nothing about one
    shop reaches another, or the person, or the reply they get.

    EACH TENANT GETS ITS OWN SAVEPOINT. One shop holding two rows for the same
    number is an ambiguity that must not cost a different shop its link, and
    must certainly not roll back the phone proof that triggered the sweep.
    """
    if not user.verified_phone or not _eligible(user):
        return SweepOutcome()

    tenant_ids = list((await db.execute(
        select(Customer.tenant_id).where(
            phone_identity(Customer.phone) == phone_identity(user.verified_phone),
        ).distinct()
    )).scalars().all())

    linked = already = needs_operator = 0
    for tenant_id in tenant_ids:
        try:
            async with db.begin_nested():
                result = await reconcile(db, user=user, tenant_id=tenant_id)
                if result.state is LinkState.linked:
                    linked += 1
                elif result.state is LinkState.already_linked:
                    already += 1
                elif result.state in NEEDS_OPERATOR:
                    needs_operator += 1
                    raise _Rollback      # leave this tenant exactly as it was
        except _Rollback:
            pass

    logger.info(
        "identity.sweep user=%s tenants=%d linked=%d already=%d needs_operator=%d",
        user.id, len(tenant_ids), linked, already, needs_operator,
    )
    return SweepOutcome(linked, already, needs_operator, len(tenant_ids))


class _Rollback(Exception):
    """Unwind one tenant's savepoint without failing the sweep."""


async def find_verified_owner(db: AsyncSession, phone: str | None) -> User | None:
    """The person who PROVED they own this number, or None.

    THE ONLY WAY to turn a phone into a Telegram identity. A typed number says
    somebody wrote it down; it does not say whose account answers it, and
    treating it as if it did means a person who types a stranger's number into
    their own profile starts receiving that stranger's messages.

    Matched through the same expression as `uq_users_verified_phone_digits`, so
    the lookup is an index probe and `+998 90 …` finds `998…`.
    """
    if not phone:
        return None
    return (await db.execute(
        select(User).where(
            User.verified_phone.is_not(None),
            phone_identity(User.verified_phone) == phone_identity(phone),
            User.phone_eligibility == PhoneEligibility.eligible.value,
        ).limit(1)
    )).scalar_one_or_none()


async def find_verified_owners(
    db: AsyncSession, phones: list[str | None],
) -> dict[str, int]:
    """``{phone identity → telegram id}`` for the proved owners of these numbers.

    One query for a whole page rather than one per row, over the same indexed
    expression as the single lookup so the two can never disagree.
    """
    wanted = [p for p in phones if p]
    if not wanted:
        return {}
    rows = (await db.execute(
        select(phone_identity(User.verified_phone), User.telegram_id).where(
            User.verified_phone.is_not(None),
            User.telegram_id.is_not(None),
            User.phone_eligibility == PhoneEligibility.eligible.value,
            phone_identity(User.verified_phone).in_(
                [phone_identity(p) for p in wanted]
            ),
        )
    )).all()
    return {key: tg for key, tg in rows}


async def link_new_customer(
    db: AsyncSession, *, customer: Customer,
) -> LinkResult:
    """Connect a just-created customer to the person who already proved its number.

    The merchant types a number they already know; if that number's owner has
    proved it to us, the record should arrive connected rather than telling the
    shop to chase a customer who is already here.

    Answers about the CALLER's own tenant only, and returns nothing about the
    matched account — the merchant learns that the record is linked, never who
    the global identity is.
    """
    if not customer.phone:
        return LinkResult(LinkState.no_match)

    owner = await find_verified_owner(db, customer.phone)
    if owner is None:
        return LinkResult(LinkState.no_match)

    return await reconcile(
        db, user=owner, tenant_id=customer.tenant_id,
        expected_customer_id=customer.id,
    )
