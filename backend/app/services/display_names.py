"""Which name to show where, and why it is that one.

Four different names exist for one person and they are not interchangeable:

  1. the Telegram profile name — the person edits it in Telegram;
  2. the name the person chose for themselves here (`User.display_name`);
  3. the shop's own label for them (`Customer.full_name`) — a merchant's own record,
     which may be "Aka Jasur" or "Jasur — telefon" and is nobody's real name;
  4. the name frozen into a signed contract.

Reading the wrong one is how a client home screen ends up greeting someone by
the Telegram profile name of the account they signed in with — which, for a
platform operator, is the operator's name. A support identity has no business
personalising a customer's own screen.
"""
from __future__ import annotations

import unicodedata

from app.models import Customer, User

#: Agrees with `users.display_name` (VARCHAR 120). A limit the column does not
#: share is a 500 waiting to happen.
MAX_DISPLAY_NAME = 120

#: Invisible by category — control, format, surrogate, private-use, unassigned.
#: A name made only of these renders as an empty greeting while the record looks
#: filled in, so it is refused rather than stored.
_INVISIBLE = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})


def normalize_display_name(raw: str | None) -> str | None:
    """The stored form of a self-chosen name, or None for "no name given".

    Server-authoritative: a client may send whatever it likes, and what lands in
    the column is decided here. Composed to NFC so one name has one spelling,
    outer whitespace trimmed, internal runs collapsed, and case preserved —
    people capitalise their own names deliberately.
    """
    if not raw:
        return None
    text = unicodedata.normalize("NFC", raw)
    kept = "".join(" " if ch == "\t" else ch
                   for ch in text if ch == "\t" or unicodedata.category(ch) not in _INVISIBLE)
    collapsed = " ".join(kept.split())
    if not collapsed:
        return None
    return collapsed[:MAX_DISPLAY_NAME]


def person_name(user: User) -> str | None:
    """The name the PERSON owns, or None.

    Self-chosen first. An operator account contributes nothing here: if the
    signed-in account carries a platform role, its Telegram profile name is an
    operator's name, and using it to greet a customer is the defect this
    function exists to prevent. Such an account has to choose a name of its own
    to be greeted by one.
    """
    chosen = (user.display_name or "").strip()
    if chosen:
        return chosen
    if user.platform_role is not None:
        return None
    telegram = " ".join(p for p in (user.first_name, user.last_name) if p).strip()
    return telegram or None


def merchant_label(customer: Customer) -> str:
    """What a shop calls this customer in its own lists.

    The tenant's own record, never overwritten by the person's profile and never
    promoted into one: a shop renaming its customer must not rename the person.
    """
    return (customer.full_name or "").strip() or "—"


def contract_party_name(customer: Customer) -> str:
    """The name a contract is issued to, captured at issue time.

    Callers snapshot the return value into the document. A signed document must
    keep saying what it said when it was signed, so nothing downstream may
    re-resolve this later.
    """
    return merchant_label(customer)
