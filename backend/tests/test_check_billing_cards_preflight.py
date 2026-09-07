"""The receiving-card preflight gate.

Shipping the compatibility rules while a configured card cannot be resolved
empties or silently thins the top-up destination list — the shop sees fewer
cards, or none, and nothing says why. This is the check that runs before the
deploy, against the same resolver the product uses.

It is read-only by construction: `evaluate()` takes cards and returns a verdict,
and the only database access in the module is one SELECT.
"""
from __future__ import annotations

import re

import pytest

from scripts.check_billing_cards import evaluate

UZCARD = {"id": "c-uz", "bank": "Kapital Bank", "number": "5614000000002802",
          "network": "UZCARD"}
HUMO_LEGACY = {"id": "c-humo", "bank": "Ipoteka Bank", "number": "9860160101234567",
               "scheme": "humo"}
VISA = {"id": "c-visa", "bank": "Kapital Bank", "number": "4413000000003478",
        "network": "VISA"}
MYSTERY = {"id": "c-x", "bank": "Kapital Bank", "number": "5614682204932899"}
CONTRADICTORY = {"id": "c-bad", "bank": "humo", "scheme": "visa",
                 "number": "4413000000003478"}


# ── it passes only when the top-up screen will actually work ────────────────

def test_a_resolvable_uzs_card_passes(t=None) -> None:
    v = evaluate([UZCARD, VISA])
    assert v["ok"] is True
    assert v["problems"] == []
    assert v["offerable"] == ["c-uz"]          # VISA settles USD, correctly excluded


def test_a_legacy_labelled_card_counts_as_resolved(t=None) -> None:
    v = evaluate([HUMO_LEGACY])
    assert v["ok"] is True
    assert v["cards"][0]["source"] == "legacy_label"
    assert v["cards"][0]["settlement_currency"] == "UZS"


def test_an_inactive_card_neither_blocks_nor_counts(t=None) -> None:
    v = evaluate([UZCARD, {**MYSTERY, "active": False}])
    assert v["ok"] is True                      # the unresolved card is inactive
    assert v["active"] == 1
    assert v["offerable"] == ["c-uz"]


# ── it fails for each of the three mandated reasons ─────────────────────────

def test_an_unresolved_active_card_fails(t=None) -> None:
    v = evaluate([UZCARD, MYSTERY])
    assert v["ok"] is False
    assert v["unresolved"] == 1
    assert any("cannot be resolved" in p for p in v["problems"])
    assert "••2899" in " ".join(v["problems"])


def test_a_card_that_contradicts_itself_fails(t=None) -> None:
    v = evaluate([UZCARD, CONTRADICTORY])
    assert v["ok"] is False
    assert v["contradictory"] == 1
    assert any("contradict themselves" in p for p in v["problems"])


def test_no_wallet_currency_card_fails(t=None) -> None:
    """USD-only destinations against a so'm wallet: nowhere to send money."""
    v = evaluate([VISA])
    assert v["ok"] is False
    assert v["offerable"] == []
    assert any("nowhere to send money" in p for p in v["problems"])


def test_no_cards_at_all_fails(t=None) -> None:
    v = evaluate([])
    assert v["ok"] is False
    assert any("nowhere to send money" in p for p in v["problems"])


# ── what it is allowed to print ─────────────────────────────────────────────

def test_no_card_number_appears_anywhere_in_the_verdict(t=None) -> None:
    cards = [UZCARD, HUMO_LEGACY, VISA, MYSTERY, CONTRADICTORY]
    blob = repr(evaluate(cards))
    for card in cards:
        assert card["number"] not in blob
    # And nothing PAN-shaped survives by another route.
    assert not re.search(r"\d{7,}", blob), blob


def test_every_mandated_field_is_reported(t=None) -> None:
    row = evaluate([UZCARD])["cards"][0]
    assert set(row) == {"id", "last4", "resolved", "network", "settlement_currency",
                        # (FB-024) the BIN's own reading, so a rejected card can
                        # be corrected without guessing which field is wrong
                        "bin_network",
                        "source", "reason", "active"}
    assert row["id"] == "c-uz"
    assert row["last4"] == "2802"
    assert row["network"] == "UZCARD"
    assert row["settlement_currency"] == "UZS"
    assert row["active"] is True
    assert row["reason"] is None


# ── it must not become a second, drifting copy of the rules ─────────────────

def test_it_agrees_with_the_endpoints_own_filter(t=None) -> None:
    from app.core.card_networks import PLATFORM_BALANCE_CURRENCY, compatible_cards

    cards = [UZCARD, HUMO_LEGACY, VISA, MYSTERY, CONTRADICTORY]
    listed = [c["id"] for c in compatible_cards(cards, PLATFORM_BALANCE_CURRENCY)]
    assert evaluate(cards)["offerable"] == listed


def test_it_never_infers_a_network_from_the_number(t=None) -> None:
    """5614 is Uzcard AND inside Mastercard's range; digits decide nothing."""
    for number in ("5614000000002802", "4413000000003478", "8600000000001234"):
        v = evaluate([{"id": "n", "number": number}])
        assert v["cards"][0]["resolved"] is False
        assert v["cards"][0]["network"] is None


def test_the_module_makes_no_outbound_calls(t=None) -> None:
    """A BIN lookup here would reintroduce exactly the guess we removed."""
    import pathlib

    src = pathlib.Path(__import__("scripts.check_billing_cards", fromlist=["x"]).__file__)
    text = src.read_text()
    for forbidden in ("http://", "https://", "requests", "httpx", "urllib", "binlist"):
        assert forbidden not in text, forbidden


def test_it_writes_nothing(t=None) -> None:
    import pathlib

    src = pathlib.Path(__import__("scripts.check_billing_cards", fromlist=["x"]).__file__)
    body = src.read_text().upper()
    for write in ("INSERT ", "UPDATE ", "DELETE ", "ALTER ", "DROP "):
        assert write not in body, write


@pytest.mark.parametrize(("cards", "code"), [
    ([UZCARD], 0),
    ([UZCARD, MYSTERY], 1),
    ([VISA], 1),
])
def test_the_verdict_maps_to_the_documented_exit_code(cards, code) -> None:
    assert (0 if evaluate(cards)["ok"] else 1) == code
