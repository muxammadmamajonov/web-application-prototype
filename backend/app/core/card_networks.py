"""Which destination cards may receive a platform balance top-up.

A receiving card settles in exactly one currency, so offering one that cannot
settle the wallet's currency invites a transfer that bounces or silently arrives
after somebody's FX, with no record of the rate.

The single authority: the list endpoint filters with it, the submission endpoint
re-checks with it, and the platform settings save normalises with it, so a caller
that skips the UI cannot reach an incompatible card.

NETWORK IS DECLARED, NEVER INFERRED. Uzcard issues in the 5614 range, inside
Mastercard's 51-55 range, so the same prefix is two networks with two settlement
currencies. `number` is never read here except for its last four digits.

Legacy cards predating the `network` field resolve from an *exact* normalised
label in a display field, and from nothing else — an issuing-bank name, an
unknown network or two labels that disagree stay unresolved and are withheld.
`normalized_card` writes the explicit fields back on the next admin save, which
ends that dependency.

Eligibility only: nothing here converts an amount.
"""
from __future__ import annotations

import re
from enum import StrEnum
from typing import Any


class CardNetwork(StrEnum):
    uzcard = "UZCARD"
    humo = "HUMO"
    visa = "VISA"
    mastercard = "MASTERCARD"


#: Settlement currency → the networks that can receive it.
#: Extending this is a business decision, not a code change scattered across
#: call sites; adding a currency here is the whole change.
COMPATIBLE_NETWORKS: dict[str, frozenset[CardNetwork]] = {
    "UZS": frozenset({CardNetwork.uzcard, CardNetwork.humo}),
    "USD": frozenset({CardNetwork.visa, CardNetwork.mastercard}),
}

#: The Muddato platform wallet settles in so'm. Named here rather than inlined
#: at the call site so the top-up screen and the card filter cannot disagree
#: about what the balance is denominated in.
PLATFORM_BALANCE_CURRENCY = "UZS"

#: Exact legacy labels that count as a network *declaration*. Deliberately tiny
#: and closed: an issuing bank ("Kapital Bank", "Ipoteka") is not a network, and
#: a network we have not approved a settlement currency for is not one either.
LEGACY_NETWORK_LABELS: dict[str, CardNetwork] = {
    "uzcard": CardNetwork.uzcard,
    "humo": CardNetwork.humo,
    "visa": CardNetwork.visa,
    "mastercard": CardNetwork.mastercard,
    "master card": CardNetwork.mastercard,
}

#: Fields that may carry a legacy label, most specific first. `scheme` is the
#: card-network slot the old admin form wrote; `bank` is the free-text issuer
#: name and almost never matches — it is read only so an operator who typed the
#: network into the wrong box is not punished for it.
LEGACY_LABEL_FIELDS: tuple[str, ...] = ("scheme", "network_label", "bank")

#: Structured, translatable rejection reasons. The client maps these to uz/ru
#: copy; they are never shown raw.
ERR_MISMATCH = "topup_card_currency_mismatch"
ERR_INACTIVE = "topup_card_inactive"
ERR_UNAVAILABLE = "topup_card_unavailable"

#: Why a card could not be resolved. Surfaced to the platform/admin card
#: configuration UI so an unresolved card is visibly broken rather than
#: silently missing from the top-up screen.
UNRESOLVED_NETWORK_INVALID = "card_network_invalid"
UNRESOLVED_NETWORK_UNDECLARED = "card_network_undeclared"
UNRESOLVED_NETWORK_AMBIGUOUS = "card_network_ambiguous"
UNRESOLVED_CURRENCY_CONFLICT = "card_settlement_currency_conflict"
#: The declared network is contradicted by the card's own BIN.
UNRESOLVED_NETWORK_BIN_CONFLICT = "card_network_bin_conflict"

_WHITESPACE = re.compile(r"\s+")


def _normalize_label(raw: object) -> str:
    """Lower-case, trim, and collapse internal runs of whitespace.

    `" Master  Card "` and `"MASTERCARD"` are the same declaration typed by two
    different people; `"Kapital Bank"` is still not a network afterwards.
    """
    if not isinstance(raw, str):
        return ""
    return _WHITESPACE.sub(" ", raw.strip().lower())


def parse_network(raw: object) -> CardNetwork | None:
    """A declared network, or None when absent/unrecognised.

    None is a refusal, not a default: an operator who mistypes a network gets a
    card withheld from every currency rather than one quietly attached to the
    wrong settlement rail.
    """
    label = _normalize_label(raw)
    if not label:
        return None
    try:
        return CardNetwork(label.upper())
    except ValueError:
        return None


def _legacy_network(card: dict) -> tuple[CardNetwork | None, str | None]:
    """Resolve a network from the legacy display labels.

    Returns ``(network, None)`` on success, or ``(None, reason)``. Two labels
    that name different networks resolve to nothing: a card that disagrees with
    itself is configuration we must not pick a winner for.
    """
    found: list[CardNetwork] = []
    for field in LEGACY_LABEL_FIELDS:
        network = LEGACY_NETWORK_LABELS.get(_normalize_label(card.get(field)))
        if network is not None and network not in found:
            found.append(network)
    if not found:
        return None, UNRESOLVED_NETWORK_UNDECLARED
    if len(found) > 1:
        return None, UNRESOLVED_NETWORK_AMBIGUOUS
    return found[0], None


def resolve_network(card: dict) -> tuple[CardNetwork | None, str | None, str | None]:
    """``(network, source, unresolved_reason)`` for one card.

    `source` is ``"explicit"`` or ``"legacy_label"`` — the admin UI shows which,
    because a legacy label was originally produced by client-side BIN detection
    and is trusted only until the operator confirms it.

    A `network` field that is present but unparseable does NOT fall back to the
    labels. Falling back would mean a typo silently resurrects the guess the
    explicit field exists to replace.
    """
    if "network" in card and _normalize_label(card.get("network")):
        network = parse_network(card.get("network"))
        if network is None:
            return None, None, UNRESOLVED_NETWORK_INVALID
        return network, "explicit", None
    network, reason = _legacy_network(card)
    if network is None:
        return None, None, reason
    return network, "legacy_label", None


def network_currency(network: CardNetwork) -> str | None:
    """The settlement currency the matrix assigns to `network`."""
    for currency, networks in COMPATIBLE_NETWORKS.items():
        if network in networks:
            return currency
    return None


def _declared_currency(card: dict) -> str | None:
    """The card's own settlement-currency field, if it states one.

    `settlement_currency` is the field this module writes; `currency` is
    accepted as the older alias so a hand-edited setting is not silently
    ignored.
    """
    for field in ("settlement_currency", "currency"):
        raw = card.get(field)
        if isinstance(raw, str) and raw.strip():
            return raw.strip().upper()
    return None


def resolve_card(card: dict) -> dict[str, Any]:
    """Full resolution of one card: network, settlement currency, provenance.

    The single entry point every caller goes through, so the list filter, the
    submission re-check and the admin validation state cannot disagree about
    what a card is.
    """
    network, source, reason = resolve_network(card)
    if network is None:
        return {"network": None, "settlement_currency": None, "source": None,
                "resolved": False, "reason": reason}

    # A declaration the card's own number contradicts is withheld. The
    # BIN never SELECTS a network — it only refuses one it cannot be.
    if bin_conflict(card, network):
        return {"network": network, "settlement_currency": None, "source": source,
                "resolved": False, "reason": UNRESOLVED_NETWORK_BIN_CONFLICT}

    derived = network_currency(network)
    declared = _declared_currency(card)
    if declared is not None and declared != derived:
        # A card claiming a currency its network cannot settle is contradictory
        # configuration; withhold it rather than trust either half.
        return {"network": network, "settlement_currency": None, "source": source,
                "resolved": False, "reason": UNRESOLVED_CURRENCY_CONFLICT}
    if derived is None:                                       # pragma: no cover
        # Unreachable while every CardNetwork sits in the matrix, which
        # `test_every_network_belongs_to_exactly_one_settlement_currency` pins.
        return {"network": network, "settlement_currency": None, "source": source,
                "resolved": False, "reason": UNRESOLVED_NETWORK_UNDECLARED}
    return {"network": network, "settlement_currency": derived, "source": source,
            "resolved": True, "reason": None}


def is_card_active(card: dict) -> bool:
    """Cards are active unless explicitly disabled.

    Absent means active because every card predating this field was in use; a
    stricter default would have withdrawn all of them on deploy.
    """
    return card.get("active", True) is not False


def card_currency(card: dict) -> str | None:
    """The card's settlement currency, or None when it cannot be resolved."""
    return resolve_card(card)["settlement_currency"]


def card_network(card: dict) -> CardNetwork | None:
    """The card's resolved network, or None when it cannot be resolved."""
    resolution = resolve_card(card)
    return resolution["network"] if resolution["resolved"] else None


def is_compatible(card: dict, currency: str) -> bool:
    """Whether `card` may receive a top-up denominated in `currency`."""
    if not is_card_active(card):
        return False
    resolution = resolve_card(card)
    if not resolution["resolved"]:
        return False
    wanted = (currency or "").strip().upper()
    allowed = COMPATIBLE_NETWORKS.get(wanted)
    if not allowed or resolution["network"] not in allowed:
        return False
    return resolution["settlement_currency"] == wanted


def compatible_cards(cards: list[dict], currency: str) -> list[dict]:
    """The subset of `cards` offerable for a `currency` top-up.

    Incompatible cards are omitted rather than returned disabled: a card shown
    at all reads as payable, and the shop is copying a number into a banking app
    where a mistake is a real transfer.
    """
    return [c for c in cards if is_compatible(c, currency)]


def rejection_code(card: dict | None, currency: str) -> str | None:
    """Why this card cannot take this top-up, or None when it can."""
    if card is None:
        return ERR_UNAVAILABLE
    if not is_card_active(card):
        return ERR_INACTIVE
    if not resolve_card(card)["resolved"]:
        return ERR_UNAVAILABLE
    return None if is_compatible(card, currency) else ERR_MISMATCH


# ── BIN cross-check ─────────────────────────────────────────────────────────
#
# Enriching a card's network from its BIN was considered and deliberately not
# done, for a reason that is still correct: inferring the network from
# the BIN would re-introduce exactly the guess the declared `network` field
# exists to remove. Uzcard issues in 5614, which sits inside Mastercard's 51-55
# range, so that prefix genuinely IS two networks with two settlement
# currencies. Nothing external can resolve that, and the deferral's preflight
# asserts this module makes no outbound call.
#
# What the BIN *can* do is disagree. A card declared VISA whose number begins
# 8600 is not ambiguous, it is wrong — and today it is accepted, which routes a
# USD top-up at a card that settles only in so'm. So the BIN is a CROSS-CHECK
# rather than a source: the declaration still decides, and the number can only
# veto a declaration it provably contradicts.
#
# Deliberately conservative. Only prefixes that belong to exactly one network
# appear here, because a false veto withholds a legitimate card — a worse
# outcome than missing a rare misconfiguration. Shared ranges (5614, and the
# whole 51-55 / 2221-2720 Mastercard space that contains it) are absent on
# purpose and can never produce a conflict.
#
# Local, versioned, offline: the first six digits are examined in memory and
# nothing is stored or sent. No PAN leaves the process.
UNAMBIGUOUS_BIN_PREFIXES: tuple[tuple[str, CardNetwork], ...] = (
    ("8600", CardNetwork.uzcard),    # Uzcard's own IIN range
    ("9860", CardNetwork.humo),      # Humo's own IIN range
    ("4", CardNetwork.visa),         # ISO/IEC 7812: 4 is Visa exclusively
)


def bin_network(card: dict) -> CardNetwork | None:
    """The network the card's BIN can only belong to, or None when ambiguous.

    None is the normal answer for a Mastercard-range card and for anything
    unrecognised, and it never means "not a card" — it means "the number does
    not settle the question", which is most of them.
    """
    digits = re.sub(r"\D", "", str(card.get("number") or ""))
    if len(digits) < 6:
        return None
    for prefix, network in UNAMBIGUOUS_BIN_PREFIXES:
        if digits.startswith(prefix):
            return network
    return None


def bin_conflict(card: dict, declared: CardNetwork | None) -> bool:
    """True when the BIN unambiguously contradicts the declared network."""
    if declared is None:
        return False
    inferred = bin_network(card)
    return inferred is not None and inferred is not declared


def last4(card: dict) -> str:
    """The last four digits, for labelling a card without exposing its PAN.

    The only place in this module that touches `number`, and it cannot identify
    a network — which is the point.
    """
    digits = re.sub(r"\D", "", str(card.get("number") or ""))
    return digits[-4:]


def card_validation_state(card: dict) -> dict[str, Any]:
    """What the platform/admin card configuration UI needs to show one card.

    Carries no PAN — an id and the last four digits are enough to point at a
    row, and this payload travels to a browser and into logs.
    """
    resolution = resolve_card(card)
    from_bin = bin_network(card)
    return {
        "id": str(card.get("id") or ""),
        "last4": last4(card),
        "resolved": resolution["resolved"],
        "network": resolution["network"].value if resolution["network"] else None,
        "settlement_currency": resolution["settlement_currency"],
        "source": resolution["source"],
        "reason": resolution["reason"],
        "active": is_card_active(card),
        # What the number says, so an operator correcting a rejected
        # card is told which of the two values to change. None for the many
        # cards whose BIN is genuinely ambiguous.
        "bin_network": from_bin.value if from_bin else None,
    }


def normalized_card(card: dict) -> dict[str, Any]:
    """`card` with its resolved network + settlement currency written in.

    This is how the legacy transition ends: the first successful admin save
    after the upgrade persists the explicit shape for every card whose label
    resolved, so no card keeps depending on a label. An unresolved card is
    returned untouched — inventing a value here is exactly the guess the module
    refuses to make.
    """
    resolution = resolve_card(card)
    if not resolution["resolved"]:
        return dict(card)
    out = dict(card)
    out["network"] = resolution["network"].value
    out["settlement_currency"] = resolution["settlement_currency"]
    out.pop("currency", None)   # folded into settlement_currency; one field, not two
    return out
