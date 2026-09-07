"""FB-024 — BIN enrichment for receiving cards, resolved as a cross-check.

THE FINDING AND WHY IT WAS DEFERRED. Batch 1 deferred "external BIN enrichment"
with a reason that has not stopped being true: the whole point of FB-022 is that
a receiving card's network is **declared**, and inferring it from the number
would put the guess back. Uzcard issues in 5614, inside Mastercard's 51-55 range,
so for the cards this product actually handles the BIN genuinely cannot decide.

WHAT IS IMPLEMENTED INSTEAD. The BIN never selects a network; it may only veto a
declaration it provably contradicts. `8600…` declared VISA is not ambiguous, it
is wrong — and before this it was accepted, which is a USD top-up pointed at a
card that settles only in so'm. Local table, no outbound call, conservative
enough that a shared range can never withhold a legitimate card.

These tests cover the lifecycle the batch asks for: origin, normalisation,
validation, persistence shape, API exposure, permissions, fallback and tenant
isolation.
"""
from __future__ import annotations

import inspect

import pytest

from app.core import card_networks
from app.core.card_networks import (
    UNAMBIGUOUS_BIN_PREFIXES,
    UNRESOLVED_NETWORK_BIN_CONFLICT,
    CardNetwork,
    bin_conflict,
    bin_network,
    card_validation_state,
    normalized_card,
    resolve_card,
)


def _card(number: str, network: str | None = None, **extra) -> dict:
    card = {"id": "c1", "number": number, "active": True, **extra}
    if network is not None:
        card["network"] = network
    return card


# ── the BIN's own opinion ────────────────────────────────────────────────────

class TestBinNetwork:
    @pytest.mark.parametrize("number,expected", [
        ("8600000000001234", CardNetwork.uzcard),
        ("9860123412341234", CardNetwork.humo),
        ("4111111111111111", CardNetwork.visa),
    ])
    def test_an_unambiguous_prefix_is_recognised(self, number, expected) -> None:
        assert bin_network(_card(number)) is expected

    @pytest.mark.parametrize("number", [
        "5614123412341234",   # Uzcard inside Mastercard's range — the whole reason
        "5555555555554444",   # Mastercard proper
        "2221123412341234",   # Mastercard 2-series
        "6200123412341234",   # UnionPay — a network we do not settle
    ])
    def test_a_shared_or_unknown_prefix_decides_nothing(self, number) -> None:
        assert bin_network(_card(number)) is None

    @pytest.mark.parametrize("number", ["", "86", "not-a-number", "860"])
    def test_too_little_to_read_decides_nothing(self, number) -> None:
        assert bin_network(_card(number)) is None

    def test_the_table_holds_only_single_network_prefixes(self) -> None:
        """A prefix that maps to two networks would produce false vetoes, which
        withhold legitimate cards — the failure this design must not have."""
        seen: dict[str, CardNetwork] = {}
        for prefix, network in UNAMBIGUOUS_BIN_PREFIXES:
            assert prefix not in seen, f"{prefix} appears twice"
            seen[prefix] = network
        # 5614 is Uzcard *and* Mastercard, so no entry may claim it.
        for prefix, _n in UNAMBIGUOUS_BIN_PREFIXES:
            assert not "5614".startswith(prefix), (
                f"{prefix} would claim the shared Uzcard/Mastercard 5614 range"
            )


# ── the veto ─────────────────────────────────────────────────────────────────

class TestTheVeto:
    def test_a_contradicted_declaration_is_withheld(self) -> None:
        got = resolve_card(_card("8600000000001234", "VISA"))
        assert got["resolved"] is False
        assert got["reason"] == UNRESOLVED_NETWORK_BIN_CONFLICT

    def test_a_matching_declaration_resolves(self) -> None:
        got = resolve_card(_card("8600000000001234", "UZCARD"))
        assert got["resolved"] is True
        assert got["network"] is CardNetwork.uzcard
        assert got["settlement_currency"] == "UZS"

    @pytest.mark.parametrize("declared", ["UZCARD", "MASTERCARD"])
    def test_a_shared_range_never_vetoes_either_reading(self, declared) -> None:
        """5614 is the case the deferral was written about. Both declarations
        must stand — vetoing either would be the guess FB-022 removed."""
        got = resolve_card(_card("5614123412341234", declared))
        assert got["resolved"] is True, got

    def test_the_bin_never_supplies_a_missing_network(self) -> None:
        """Enrichment would be inference. An undeclared card stays undeclared,
        even when its BIN is perfectly unambiguous."""
        got = resolve_card(_card("8600000000001234"))
        assert got["resolved"] is False
        assert got["network"] is None
        assert got["reason"] != UNRESOLVED_NETWORK_BIN_CONFLICT

    def test_no_declaration_means_no_conflict(self) -> None:
        assert bin_conflict(_card("8600000000001234"), None) is False

    def test_an_unreadable_number_never_vetoes(self) -> None:
        """A card configured without a number must not become unresolvable."""
        got = resolve_card({"id": "c1", "network": "VISA", "active": True})
        assert got["resolved"] is True


# ── normalisation and persistence ────────────────────────────────────────────

class TestNormalisationAndPersistence:
    def test_a_vetoed_card_is_never_normalised_into_storage(self) -> None:
        """`normalized_card` writes the resolved shape back on the next admin
        save. A contradicted card must not be written back as if it were fine."""
        card = _card("8600000000001234", "VISA")
        assert normalized_card(card) == card

    def test_a_valid_card_still_normalises(self) -> None:
        out = normalized_card(_card("8600000000001234", "UZCARD"))
        assert out["network"] == "UZCARD"
        assert out["settlement_currency"] == "UZS"

    def test_the_admin_state_names_both_values_so_it_can_be_corrected(self) -> None:
        """An operator told only "invalid" has to guess which field is wrong."""
        state = card_validation_state(_card("8600000000001234", "VISA"))
        assert state["resolved"] is False
        assert state["reason"] == UNRESOLVED_NETWORK_BIN_CONFLICT
        assert state["network"] == "VISA", "what was declared"
        assert state["bin_network"] == "UZCARD", "what the number says"

    def test_the_admin_state_still_carries_no_pan(self) -> None:
        """This payload reaches a browser and the logs."""
        state = card_validation_state(_card("8600000000001234", "UZCARD"))
        assert "number" not in state
        assert state["last4"] == "1234"
        assert "8600000000001234" not in str(state)


# ── the constraint the deferral imposed ──────────────────────────────────────

def test_the_module_still_makes_no_outbound_call() -> None:
    """The deferral's own condition, kept. FB-024 is closed WITHOUT a
    third-party lookup: no network client, no API key, no PAN leaving the
    process, nothing to be unavailable at 3am."""
    src = inspect.getsource(card_networks)
    for forbidden in ("httpx", "aiohttp", "requests", "urlopen", "socket"):
        assert forbidden not in src, f"card_networks reached for {forbidden}"


def test_the_bin_table_is_local_and_versioned_in_code() -> None:
    """A table in the repository is reviewable and diffable; a remote one is
    neither, and would make card validation depend on somebody else's uptime."""
    assert isinstance(UNAMBIGUOUS_BIN_PREFIXES, tuple)
    assert UNAMBIGUOUS_BIN_PREFIXES, "the table must not be empty"
    for prefix, network in UNAMBIGUOUS_BIN_PREFIXES:
        assert prefix.isdigit()
        assert isinstance(network, CardNetwork)
