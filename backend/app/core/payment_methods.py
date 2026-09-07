"""Which payment methods the product currently offers (#14).

Two separate surfaces decide what a payment method means:

  * the tenant's own settings screen, where an owner switches methods on;
  * `GET /me/installments/{id}/payment-methods`, which tells a CUSTOMER what
    they may pay with.

Withdrawing a method from one and not the other is the obvious failure: a
customer offered Payme by a screen the owner can no longer configure, or a
toggle that turns on something nobody can use. So the offered set lives here,
once, and `paymentMethods.contract.test.ts` pins the frontend list against it.

TEMPORARILY is meant literally. `ALL_METHODS` is the full vocabulary and does
not shrink; `OFFERED` is the subset the product currently exposes. Restoring a
method is a one-word change here, and — because nothing in this module writes
to the database — every tenant that had it configured gets it back exactly as
they left it. Stored `payment_methods` and provider credentials are never
rewritten to match this list; they are only filtered on read.
"""
from __future__ import annotations

# The complete vocabulary. Entries are never removed — a stored config may
# reference any of them, and `transfer` still appears in rows written before
# #14 even though nothing offers it today.
ALL_METHODS: tuple[str, ...] = ("cash", "card", "transfer", "click", "payme")

# What the product offers right now. #14 withdrew Payme, Click and Bank
# Transfer while the Telegram-first flow settles, leaving cash and card.
OFFERED: tuple[str, ...] = ("cash", "card")

WITHDRAWN: tuple[str, ...] = tuple(m for m in ALL_METHODS if m not in OFFERED)


def is_offered(method: str) -> bool:
    return method in OFFERED


def card_is_usable(payment_methods: dict | None) -> bool:
    """Card is only a real option once there is somewhere for the money to go.

    A tenant can switch Card on and save before adding any receiving card, which
    would otherwise offer the customer a method that renders an empty card list
    and no way to pay. The presence of a card is therefore part of the
    availability test, not a separate warning.
    """
    pm = payment_methods or {}
    if not pm.get("card"):
        return False
    cards = pm.get("cards")
    return isinstance(cards, list) and len(cards) > 0
