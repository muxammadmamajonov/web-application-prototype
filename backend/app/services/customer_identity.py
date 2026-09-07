"""Is this customer reachable on Telegram, and as whom?

A shop entering a customer is in one of three situations:

  ``joined``  the customer record carries a Telegram id — they are linked to
              THIS shop and can open their contracts.
  ``known``   no link here, but somebody has PROVED they own this number, so
              they are reachable right now and should get a message rather than
              a link for the operator to pass on by hand.
  ``new``     nobody has proved this number.

WHAT COUNTS AS KNOWING SOMEBODY. This used to match on `users.phone` — the
number a person typed into their own profile. That is not evidence of anything:
anyone could type a stranger's number and become, as far as this module was
concerned, the account behind it. That made it a way to receive somebody else's
contract notifications and invitations.

Only `users.verified_phone` answers the question now, and only when its
eligibility says the number can trade. The lookup itself lives in
`identity_link` so that this module, customer creation and reconciliation cannot
drift into three different opinions about who owns a number.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Customer, User
from app.services.identity_link import find_verified_owner, find_verified_owners

#: Statuses as they cross the wire. Plain strings, because the frontend
#: switches on them and an enum would only add a translation step.
JOINED = "joined"
KNOWN = "known"
NEW = "new"


def _key(phone: str | None) -> str | None:
    """The identity `phone_identity` computes for this number, or None.

    None for anything that is not a usable UZ mobile — a number this market
    cannot serve has no business matching a batch lookup, and returning a key
    for it would invite a caller to treat "not found" as "not eligible" when
    the two are different answers.

    For UZ numbers this produces exactly what the SQL expression produces, so a
    batch lookup's keys and a row's key are comparable.
    """
    from app.services.sms_eskiz import canonical_uz_phone

    canonical = canonical_uz_phone(phone)
    return None if canonical is None else canonical.lstrip("+")


async def find_verified_user(db: AsyncSession, phone: str | None) -> User | None:
    """The person who proved they own this number. One shared implementation."""
    return await find_verified_owner(db, phone)


async def known_telegram_ids(
    db: AsyncSession, phones: list[str | None],
) -> dict[str, int]:
    """``{identity key → telegram id}`` for the PROVED owners of these numbers.

    One query for a whole page rather than one per row.
    """
    return await find_verified_owners(db, phones)


def status_for(customer: Customer, known: dict[str, int]) -> str:
    """Which of the three situations this customer is in, given a batch lookup."""
    if customer.telegram_id is not None:
        return JOINED
    key = _key(customer.phone)
    return KNOWN if key is not None and key in known else NEW


async def reachable_telegram_id(db: AsyncSession, customer: Customer) -> int | None:
    """Which Telegram account may be told about this customer, or None.

    Their own linked account first. Failing that, the account that PROVED it
    owns the customer's number — never merely one that typed it. Sending a
    shop's invitation, or news of a contract, to the wrong person is the
    concrete harm this ordering exists to prevent.
    """
    if customer.telegram_id is not None:
        return customer.telegram_id
    owner = await find_verified_owner(db, customer.phone)
    return owner.telegram_id if owner is not None else None


__all__ = [
    "JOINED", "KNOWN", "NEW",
    "find_verified_user", "known_telegram_ids", "reachable_telegram_id", "status_for",
]