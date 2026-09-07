"""Turning a Telegram contact message into proof that someone owns a number.

Telegram's Mini App `initData` carries an account's id and profile, and NOT its
phone. The only in-Telegram way to learn the number is for the person to share
their contact, which arrives here as an ordinary bot update.

WHAT MAKES IT PROOF. A contact message can carry ANY contact in the sender's
address book — sharing a friend's card is a normal thing to do. The single field
that distinguishes "this is me" from "this is someone I know" is
`contact.user_id`: Telegram sets it to the contact's own Telegram account id,
and only when that equals the sender does the message say anything about the
sender's own number. Everything else here follows from that one check.

The client is never consulted. A browser can call `requestContact()` and learn
whether the person tapped "Share", but the phone itself arrives on this path,
authenticated as a Telegram update, or it does not arrive at all.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.phone_identity import PhoneEligibility, classify, mask, same_number
from app.models import User

logger = logging.getLogger("muddato.identity")

#: Recorded on `User.verified_phone_source`.
SOURCE_TELEGRAM_CONTACT = "telegram_contact"


class ContactOutcome(StrEnum):
    verified = "VERIFIED"
    #: Same number, already proved. Telegram retries; people tap twice.
    unchanged = "UNCHANGED"
    #: Shared someone else's card, or Telegram omitted the ownership field.
    not_owner = "CONTACT_NOT_OWNER"
    #: Not a phone number.
    malformed = "CONTACT_MALFORMED"
    #: A DIFFERENT number is already proved for this account. Changing a proved
    #: number is a support action, not something a second message may do.
    conflict = "CONTACT_CONFLICT"
    #: Somebody else already proved this number.
    taken = "CONTACT_PHONE_TAKEN"
    #: No account for this sender yet.
    unknown_sender = "CONTACT_UNKNOWN_SENDER"


@dataclass(frozen=True, slots=True)
class ContactResult:
    outcome: ContactOutcome
    eligibility: PhoneEligibility | None = None

    @property
    def ok(self) -> bool:
        return self.outcome in {ContactOutcome.verified, ContactOutcome.unchanged}


async def record_verified_contact(
    db: AsyncSession,
    *,
    sender_telegram_id: int,
    contact_user_id: int | None,
    raw_phone: str | None,
) -> ContactResult:
    """Record a proved phone for `sender_telegram_id`, or say why not.

    Commits nothing — the caller owns the transaction, so a bot handler can keep
    the write and its reply in one unit.
    """
    # The ownership check, before anything is parsed or looked up. An absent
    # `user_id` is the same answer as a mismatched one: no ownership was shown.
    if contact_user_id is None or contact_user_id != sender_telegram_id:
        logger.info(
            "identity.contact_rejected reason=not_owner sender=%s owner_present=%s",
            sender_telegram_id, contact_user_id is not None,
        )
        return ContactResult(ContactOutcome.not_owner)

    e164, eligibility = classify(raw_phone)
    if e164 is None:
        logger.info("identity.contact_rejected reason=malformed sender=%s", sender_telegram_id)
        return ContactResult(ContactOutcome.malformed, eligibility)

    user = (await db.execute(
        select(User).where(User.telegram_id == sender_telegram_id).with_for_update()
    )).scalar_one_or_none()
    if user is None:
        return ContactResult(ContactOutcome.unknown_sender, eligibility)

    if user.verified_phone:
        if same_number(user.verified_phone, e164):
            # A retry, a double tap, or Telegram redelivering. Idempotent.
            return ContactResult(ContactOutcome.unchanged, eligibility)
        logger.warning(
            "identity.contact_conflict user=%s held=%s offered=%s",
            user.id, mask(user.verified_phone), mask(e164),
        )
        return ContactResult(ContactOutcome.conflict, eligibility)

    try:
        # Savepoint first, then the write: `begin_nested()` flushes before it
        # establishes the savepoint, so assigning outside would push the UPDATE
        # ahead of the thing meant to contain it.
        async with db.begin_nested():
            user.verified_phone = e164
            user.verified_phone_at = datetime.now(UTC)
            user.verified_phone_source = SOURCE_TELEGRAM_CONTACT
            user.phone_eligibility = eligibility.value
            await db.flush()
    except IntegrityError:
        # `uq_users_verified_phone_digits` — another account proved it first.
        # Two proofs for one number cannot both be true; neither is silently
        # preferred here.
        db.expire(user)
        logger.warning("identity.verified_phone_taken sender=%s phone=%s",
                       sender_telegram_id, mask(e164))
        return ContactResult(ContactOutcome.taken, eligibility)

    logger.info(
        "identity.phone_verified user=%s phone=%s eligibility=%s",
        user.id, mask(e164), eligibility.value,
    )
    return ContactResult(ContactOutcome.verified, eligibility)
