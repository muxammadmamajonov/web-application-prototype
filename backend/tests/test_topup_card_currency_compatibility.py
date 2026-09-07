"""A top-up may only be offered a card that can settle the wallet's currency.

REPRODUCED ON A REAL DEVICE (founder beta, 2026-08-09). "Balansni to'ldirish"
showed `Joriy balans: 200 000 UZS` and then listed a VISA card and an Uzcard
card side by side. VISA settles USD; the shop was being invited to transfer
so'm to a card that cannot receive them.

Nothing in the product decided that — the card list was returned unfiltered and
the submission endpoint only checked that the id existed, so both the UI and the
API would take a VISA transfer against a so'm wallet.

The BIN cannot be used to repair this. The Uzcard on that screen was
`5614 6822 …`, and 5614 sits inside Mastercard's 51-55 range: the same prefix is
two networks with two settlement currencies. Network has to be declared, and a
card that does not declare one is withheld rather than guessed.
"""
from __future__ import annotations

import pytest

from app.core.card_networks import (
    ERR_INACTIVE,
    ERR_MISMATCH,
    ERR_UNAVAILABLE,
    UNRESOLVED_CURRENCY_CONFLICT,
    UNRESOLVED_NETWORK_AMBIGUOUS,
    UNRESOLVED_NETWORK_INVALID,
    UNRESOLVED_NETWORK_UNDECLARED,
    CardNetwork,
    card_currency,
    card_validation_state,
    compatible_cards,
    is_compatible,
    last4,
    normalized_card,
    rejection_code,
    resolve_card,
)

UZCARD = {"id": "c-uz", "bank": "Kapital", "number": "5614000000002802", "network": "UZCARD"}
HUMO = {"id": "c-humo", "bank": "Ipoteka", "number": "9860160101234567", "network": "HUMO"}
VISA = {"id": "c-visa", "bank": "Kapital", "number": "4413000000003478", "network": "VISA"}
MC = {"id": "c-mc", "bank": "Kapital", "number": "5199123412341234", "network": "MASTERCARD"}
ALL = [UZCARD, HUMO, VISA, MC]


# ── the matrix ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("currency", "card", "allowed"),
    [
        ("UZS", UZCARD, True),
        ("UZS", HUMO, True),
        ("UZS", VISA, False),
        ("UZS", MC, False),
        ("USD", VISA, True),
        ("USD", MC, True),
        ("USD", UZCARD, False),
        ("USD", HUMO, False),
    ],
)
def test_only_networks_that_settle_the_currency_are_compatible(currency, card, allowed) -> None:
    assert is_compatible(card, currency) is allowed


def test_a_uzs_topup_is_never_offered_a_visa_card() -> None:
    """The exact screenshot defect."""
    offered = {c["id"] for c in compatible_cards(ALL, "UZS")}
    assert offered == {"c-uz", "c-humo"}, offered
    assert "c-visa" not in offered


def test_a_usd_topup_is_never_offered_an_uzcard() -> None:
    offered = {c["id"] for c in compatible_cards(ALL, "USD")}
    assert offered == {"c-visa", "c-mc"}, offered


# ── refusals ────────────────────────────────────────────────────────────────

def test_an_inactive_card_is_withheld_even_when_the_network_matches() -> None:
    card = {**UZCARD, "active": False}
    assert is_compatible(card, "UZS") is False
    assert rejection_code(card, "UZS") == ERR_INACTIVE


def test_a_card_with_no_declared_network_is_withheld_from_every_currency() -> None:
    """Absent network is unknown, and unknown is refused — never inferred.

    This is the Uzcard/Mastercard BIN collision: `5614` alone cannot say which
    network, so a card without the field is offered to nobody.
    """
    card = {"id": "c-x", "bank": "Some bank", "number": "5614000000002802"}
    assert card_currency(card) is None
    for currency in ("UZS", "USD"):
        assert is_compatible(card, currency) is False
    assert rejection_code(card, "UZS") == ERR_UNAVAILABLE


def test_an_unrecognised_network_string_is_withheld() -> None:
    card = {**UZCARD, "network": "MIR"}
    assert is_compatible(card, "UZS") is False
    assert rejection_code(card, "UZS") == ERR_UNAVAILABLE


def test_a_card_claiming_a_currency_its_network_cannot_settle_is_withheld() -> None:
    """Contradictory configuration is refused rather than half-trusted."""
    card = {**VISA, "currency": "UZS"}
    assert is_compatible(card, "UZS") is False
    assert is_compatible(card, "USD") is False


def test_an_unknown_wallet_currency_offers_nothing() -> None:
    assert compatible_cards(ALL, "EUR") == []
    assert compatible_cards(ALL, "") == []


def test_rejection_code_distinguishes_mismatch_from_unavailable() -> None:
    assert rejection_code(VISA, "UZS") == ERR_MISMATCH
    assert rejection_code(None, "UZS") == ERR_UNAVAILABLE
    assert rejection_code(UZCARD, "UZS") is None


# ── derived currency ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("card", "expected"),
    [(UZCARD, "UZS"), (HUMO, "UZS"), (VISA, "USD"), (MC, "USD")],
)
def test_settlement_currency_is_derived_from_the_declared_network(card, expected) -> None:
    assert card_currency(card) == expected


def test_case_and_padding_in_a_declared_network_are_tolerated() -> None:
    assert is_compatible({**UZCARD, "network": " uzcard "}, "UZS") is True


def test_every_network_belongs_to_exactly_one_settlement_currency() -> None:
    """No network may straddle two currencies, or `card_currency` is ambiguous."""
    from app.core.card_networks import COMPATIBLE_NETWORKS

    seen: dict[CardNetwork, str] = {}
    for currency, networks in COMPATIBLE_NETWORKS.items():
        for network in networks:
            assert network not in seen, f"{network} in both {seen[network]} and {currency}"
            seen[network] = currency
    assert set(seen) == set(CardNetwork), "a network is missing from the matrix"


# ── legacy transition ───────────────────────────────────────────────────────
#
# Cards stored before `network` existed carry a display label. Withholding all
# of them on deploy would empty the production top-up screen, so an EXACT
# normalised label from the four known networks counts as a declaration — and
# nothing else does.

#: The shape production stores today: id / bank / number / scheme, no network.
LEGACY_UZCARD = {"id": "l-uz", "bank": "Kapital Bank", "number": "5614000000002802", "scheme": "uzcard"}
LEGACY_HUMO = {"id": "l-humo", "bank": "Ipoteka Bank", "number": "9860160101234567", "scheme": "humo"}
LEGACY_VISA = {"id": "l-visa", "bank": "Kapital Bank", "number": "4413000000003478", "scheme": "visa"}
LEGACY_MC = {"id": "l-mc", "bank": "Kapital Bank", "number": "5199123412341234", "scheme": "mastercard"}
LEGACY_ALL = [LEGACY_UZCARD, LEGACY_HUMO, LEGACY_VISA, LEGACY_MC]


@pytest.mark.parametrize(
    ("label", "network", "currency"),
    [
        ("uzcard", CardNetwork.uzcard, "UZS"),
        ("humo", CardNetwork.humo, "UZS"),
        ("visa", CardNetwork.visa, "USD"),
        ("mastercard", CardNetwork.mastercard, "USD"),
        ("master card", CardNetwork.mastercard, "USD"),
    ],
)
def test_each_exact_legacy_label_resolves_to_its_network(label, network, currency) -> None:
    card = {"id": "l", "number": "0000000000000000", "scheme": label}
    resolved = resolve_card(card)
    assert resolved["resolved"] is True
    assert resolved["network"] is network
    assert resolved["settlement_currency"] == currency
    assert resolved["source"] == "legacy_label"


@pytest.mark.parametrize("label", ["UZCARD", " Uzcard ", "uzCard", "  UZCARD  "])
def test_legacy_label_case_and_spacing_are_normalised(label) -> None:
    assert card_currency({"id": "l", "scheme": label}) == "UZS"


def test_master_card_spelled_with_a_space_and_odd_casing_resolves() -> None:
    assert resolve_card({"id": "l", "scheme": " Master   Card "})["network"] is CardNetwork.mastercard


def test_an_issuer_bank_name_is_never_read_as_a_network() -> None:
    """`bank` holds "Kapital Bank" / "Ipoteka" — issuers, not networks."""
    for issuer in ("Kapital Bank", "Ipoteka Bank", "NBU", "Aloqabank", "TBC"):
        card = {"id": "l", "number": "5614000000002802", "bank": issuer}
        resolved = resolve_card(card)
        assert resolved["resolved"] is False, issuer
        assert resolved["reason"] == UNRESOLVED_NETWORK_UNDECLARED
        assert is_compatible(card, "UZS") is False


def test_a_numeric_prefix_never_selects_a_network() -> None:
    """The founder's Uzcard is 5614…, which is inside Mastercard's 51-55 range.

    Every unlabelled card below is offered to nobody, whatever its digits say.
    """
    for number in ("5614000000002802", "4413000000003478", "8600000000001234",
                   "9860160101234567", "5199123412341234"):
        card = {"id": "l", "number": number}
        assert resolve_card(card)["resolved"] is False, number
        assert compatible_cards([card], "UZS") == []
        assert compatible_cards([card], "USD") == []


def test_an_unknown_legacy_label_stays_unresolved() -> None:
    """UnionPay/Maestro/Mir are real schemes with no approved settlement rail."""
    for scheme in ("unionpay", "maestro", "mir", "amex", ""):
        card = {"id": "l", "number": "6200000000000000", "scheme": scheme}
        assert resolve_card(card)["resolved"] is False, scheme


def test_two_legacy_labels_that_disagree_resolve_to_nothing() -> None:
    card = {"id": "l", "scheme": "visa", "bank": "humo"}
    resolved = resolve_card(card)
    assert resolved["resolved"] is False
    assert resolved["reason"] == UNRESOLVED_NETWORK_AMBIGUOUS


def test_an_explicit_network_overrides_a_disagreeing_legacy_label() -> None:
    card = {"id": "l", "number": "5614000000002802", "scheme": "uzcard", "network": "MASTERCARD"}
    resolved = resolve_card(card)
    assert resolved["network"] is CardNetwork.mastercard
    assert resolved["source"] == "explicit"
    assert resolved["settlement_currency"] == "USD"
    assert is_compatible(card, "UZS") is False
    assert is_compatible(card, "USD") is True


def test_an_invalid_explicit_network_is_rejected_and_never_falls_back() -> None:
    """A typo must not silently resurrect the label the field exists to replace."""
    card = {"id": "l", "scheme": "uzcard", "network": "UZCRAD"}
    resolved = resolve_card(card)
    assert resolved["resolved"] is False
    assert resolved["reason"] == UNRESOLVED_NETWORK_INVALID
    assert is_compatible(card, "UZS") is False
    assert rejection_code(card, "UZS") == ERR_UNAVAILABLE


def test_an_unresolved_card_can_never_be_submitted() -> None:
    """Both gates refuse it: it is not listed, and the id is re-checked."""
    card = {"id": "l", "bank": "Kapital Bank", "number": "5614000000002802"}
    assert compatible_cards([card], "UZS") == []
    assert rejection_code(card, "UZS") == ERR_UNAVAILABLE


def test_the_declared_legacy_cards_survive_the_upgrade() -> None:
    """The regression that would have emptied the production top-up screen."""
    offered = {c["id"] for c in compatible_cards(LEGACY_ALL, "UZS")}
    assert offered == {"l-uz", "l-humo"}, offered
    assert {c["id"] for c in compatible_cards(LEGACY_ALL, "USD")} == {"l-visa", "l-mc"}


def test_a_declared_currency_contradicting_the_legacy_label_is_withheld() -> None:
    card = {**LEGACY_VISA, "settlement_currency": "UZS"}
    resolved = resolve_card(card)
    assert resolved["resolved"] is False
    assert resolved["reason"] == UNRESOLVED_CURRENCY_CONFLICT


# ── persisting the explicit shape ───────────────────────────────────────────

def test_normalizing_a_legacy_card_writes_the_explicit_fields() -> None:
    out = normalized_card(LEGACY_UZCARD)
    assert out["network"] == "UZCARD"
    assert out["settlement_currency"] == "UZS"
    # untouched fields ride along; the label is kept for display
    assert out["number"] == LEGACY_UZCARD["number"]
    assert out["scheme"] == "uzcard"
    # and the normalised card no longer depends on the label
    assert resolve_card({k: v for k, v in out.items() if k != "scheme"})["resolved"] is True


def test_normalizing_folds_the_old_currency_alias_into_one_field() -> None:
    out = normalized_card({**LEGACY_VISA, "currency": "USD"})
    assert out["settlement_currency"] == "USD"
    assert "currency" not in out


def test_normalizing_an_unresolved_card_invents_nothing() -> None:
    card = {"id": "l", "bank": "Kapital Bank", "number": "5614000000002802"}
    assert normalized_card(card) == card


# ── what the admin UI is told ───────────────────────────────────────────────

def test_validation_state_names_the_card_without_exposing_its_number() -> None:
    state = card_validation_state(LEGACY_UZCARD)
    assert state == {
        "id": "l-uz", "last4": "2802", "resolved": True, "network": "UZCARD",
        "settlement_currency": "UZS", "source": "legacy_label", "reason": None,
        "active": True,
        # (FB-024) What the NUMBER says, alongside what was declared, so a
        # rejected card can be corrected without guessing which field is wrong.
        # None here because 5614 is the shared Uzcard/Mastercard range — the
        # exact ambiguity that stopped FB-024 becoming an inference.
        "bin_network": None,
    }
    assert LEGACY_UZCARD["number"] not in str(state)


def test_validation_state_reports_why_a_card_is_withheld() -> None:
    state = card_validation_state({"id": "x", "bank": "Kapital Bank", "number": "5614000000002802"})
    assert state["resolved"] is False
    assert state["reason"] == UNRESOLVED_NETWORK_UNDECLARED
    assert state["network"] is None
    assert state["last4"] == "2802"


def test_last4_tolerates_grouped_and_missing_numbers() -> None:
    assert last4({"number": "5614 6822 0493 2802"}) == "2802"
    assert last4({}) == ""


# ── the platform admin save gate ────────────────────────────────────────────
#
# The list endpoint filters and the submission endpoint re-checks, but neither
# stops an operator STORING a card that can never be offered. Without this gate
# the failure is invisible: the card exists in settings and appears nowhere.

def test_saving_a_card_with_no_resolvable_network_is_refused() -> None:
    from fastapi import HTTPException

    from app.api.platform.provisioning import _validated_billing_cards

    with pytest.raises(HTTPException) as ex:
        _validated_billing_cards({"items": [
            LEGACY_UZCARD,
            {"id": "bad", "bank": "Kapital Bank", "number": "5614000000002802"},
        ]})
    assert ex.value.status_code == 400
    detail = ex.value.detail
    assert detail["error"] == "billing_card_network_required"
    assert [c["id"] for c in detail["cards"]] == ["bad"]
    assert detail["cards"][0]["last4"] == "2802"
    # The rejection travels to a browser and into logs — no PANs in it.
    assert "5614000000002802" not in str(detail)


def test_a_successful_save_persists_the_explicit_shape() -> None:
    """Point 9 of the transition policy: the label stops being load-bearing."""
    from app.api.platform.provisioning import _validated_billing_cards

    out = _validated_billing_cards({"items": LEGACY_ALL})
    assert [(c["network"], c["settlement_currency"]) for c in out["items"]] == [
        ("UZCARD", "UZS"), ("HUMO", "UZS"), ("VISA", "USD"), ("MASTERCARD", "USD"),
    ]


def test_a_non_card_setting_is_stored_untouched() -> None:
    from app.api.platform.provisioning import _validated_billing_cards

    assert _validated_billing_cards({"v": "anything"}) == {"v": "anything"}
