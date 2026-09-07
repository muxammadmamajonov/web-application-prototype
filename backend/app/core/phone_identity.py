"""What a phone number means as an identity, and who is allowed to be one.

Three questions are deliberately kept apart, because conflating them is how a
verified foreign number ends up either silently accepted or hard-deleted:

    is it a well-formed number at all?      -> to_e164
    is it in the market this product serves? -> classify
    do we believe the person owns it?        -> proved elsewhere (contact/OIDC),
                                                never inferred here

NOTHING in this module establishes ownership. A number that parses is still
just a string somebody typed until an ownership proof arrives.

The Uzbek rule reuses `normalize_uz_phone`, the parser that already backs every
phone column and the functional unique indexes over them. That matters more than
a richer library would: two components that disagree about what "the same
number" means reintroduce the duplicate rows those indexes exist to prevent.
"""
from __future__ import annotations

import re
from enum import StrEnum

#: E.164 allows at most 15 digits, and a country code never starts with 0.
_E164_MAX_DIGITS = 15
_E164_MIN_DIGITS = 8
_UZ_CC = "998"


class PhoneEligibility(StrEnum):
    """Why a number may or may not become an active Muddato customer.

    Stored and logged as a code, never as prose: an operator screen and two
    locales have to render it, and a sentence in a column cannot be translated.
    """

    eligible = "ELIGIBLE"
    #: Parses as a real international number, just not one this market serves.
    unsupported_region = "UNSUPPORTED_REGION"
    #: Not a phone number at all.
    malformed = "MALFORMED"


def to_e164(raw: str | None) -> str | None:
    """Canonical ``+<digits>``, or None when the input is not a phone number.

    Structural only — length and leading digit. Deciding whether a subscriber
    number is *assigned* is a per-country question with no authoritative offline
    answer, and guessing at it would reject real customers.
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    # A bare local Uzbek number is the common input here and has no country code.
    if len(digits) == 9:
        digits = _UZ_CC + digits
    if not (_E164_MIN_DIGITS <= len(digits) <= _E164_MAX_DIGITS):
        return None
    if digits[0] == "0":
        return None
    return f"+{digits}"


def is_uz(e164: str | None) -> bool:
    """True for the +998 numbering plan, judged the same way the DB judges it.

    Not `startswith("+998")`: that accepts `+9985` and `+998123`, neither of
    which is a number anyone can be reached on, and both of which would occupy
    a real customer's slot in the tenant-unique phone index.
    """
    if not e164:
        return False
    from app.services.sms_eskiz import normalize_uz_phone

    return normalize_uz_phone(e164) is not None


def classify(raw: str | None) -> tuple[str | None, PhoneEligibility]:
    """``(canonical_e164_or_None, reason)``.

    A foreign number is returned canonicalised alongside its reason. It is a
    real number belonging to a real person; the account keeps it and is told
    the region is not served yet. Discarding it would lose the only evidence
    of why the account cannot activate.
    """
    e164 = to_e164(raw)
    if e164 is None:
        return None, PhoneEligibility.malformed
    if not is_uz(e164):
        return e164, PhoneEligibility.unsupported_region
    return e164, PhoneEligibility.eligible


def account_phone(user: object) -> str | None:
    """THE phone number of an account. One question, one answer, one column.

    The product rule is `one user → one canonical verified phone number →
    verified against that user's Telegram identity`, so this reads
    ``verified_phone`` and nothing else. It deliberately does NOT fall back to
    the typed ``users.phone``: a fallback is what made two columns into two
    answers, and it would let a number nobody proved be served as the account's
    identity the moment the proved one is absent.

    ``users.phone`` survives as what it always actually was — a contact detail
    somebody typed, kept for the legacy rows that hold one — and no longer
    answers "who is this account?". None here means "this account has not proved
    a number yet", which is a real and reportable state, not a gap to paper over.
    """
    return getattr(user, "verified_phone", None) or None


def same_number(a: str | None, b: str | None) -> bool:
    """Whether two inputs denote one subscriber, whatever their formatting."""
    ca, cb = to_e164(a), to_e164(b)
    return ca is not None and ca == cb


def mask(e164: str | None) -> str:
    """A phone safe to put in a log line, an audit row or an operator screen.

    Keeps the country code and the last two digits — enough for a human to
    recognise a number they already know, not enough to learn one they do not.
    """
    if not e164:
        return "—"
    digits = re.sub(r"\D", "", e164)
    if len(digits) < 5:
        return "•" * len(digits)
    head = digits[:3] if digits.startswith(_UZ_CC) else digits[:2]
    tail = digits[-2:]
    return f"+{head}{'•' * (len(digits) - len(head) - 2)}{tail}"
