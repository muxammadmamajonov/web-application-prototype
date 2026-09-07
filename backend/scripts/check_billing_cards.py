"""Pre-deployment gate: can every configured receiving card actually be offered?

    python -m scripts.check_billing_cards           # human-readable table
    python -m scripts.check_billing_cards --json    # machine-readable

READ-ONLY. It opens one SELECT and writes nothing — not the setting, not a log
line, not a metric. Fixing a card is an operator action in the platform settings
UI, deliberately not something this can do for you.

It resolves each card through `app.core.card_networks`, the same module the
top-up list endpoint filters with and the submission endpoint re-checks with, so
a card this reports as offerable is exactly a card that screen will show. A
second, hand-written copy of the rules here would be able to disagree with the
running product, which is the whole thing worth preventing.

Exit codes:
    0  every active card resolves, and at least one can settle the platform
       wallet's currency
    1  an active card cannot be resolved, an active card's declarations
       contradict each other, or nothing can receive a wallet-currency transfer
    2  the setting could not be read

Card numbers are never printed. Last four digits identify a row to a person
holding the physical card; the full PAN would put a payment credential into a
terminal, a CI log and a screenshot.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from sqlalchemy import create_engine, text

from app.config import settings
from app.core.card_networks import (
    PLATFORM_BALANCE_CURRENCY,
    UNRESOLVED_CURRENCY_CONFLICT,
    UNRESOLVED_NETWORK_AMBIGUOUS,
    card_validation_state,
    is_compatible,
)
from app.services.subscription_checkout import BILLING_CARDS_KEY

#: Reasons that mean the card's own declarations disagree, as opposed to simply
#: being absent. Called out separately because the operator fix differs: one is
#: "choose a network", the other is "decide which of these two is true".
_CONTRADICTIONS = frozenset({UNRESOLVED_NETWORK_AMBIGUOUS, UNRESOLVED_CURRENCY_CONFLICT})

_REASON_HELP = {
    "card_network_undeclared": "no network declared — choose one in platform settings",
    "card_network_invalid": "the declared network is not a network we settle",
    UNRESOLVED_NETWORK_AMBIGUOUS: "two declarations name different networks",
    UNRESOLVED_CURRENCY_CONFLICT: "declared currency is one this network cannot settle",
}


def _load_cards() -> list[dict[str, Any]]:
    engine = create_engine(settings.database_url_sync, future=True)
    try:
        with engine.connect() as conn:
            value = conn.execute(
                text("SELECT value FROM platform_settings WHERE key = :k"),
                {"k": BILLING_CARDS_KEY},
            ).scalar()
    finally:
        engine.dispose()
    if value is None:
        return []
    items = value.get("items") if isinstance(value, dict) else value
    if not isinstance(items, list):
        raise ValueError(f"{BILLING_CARDS_KEY} is not a list of cards")
    return [c for c in items if isinstance(c, dict)]


def evaluate(cards: list[dict[str, Any]]) -> dict[str, Any]:
    """The whole verdict, as data — so the tests assert on this, not on stdout."""
    rows = [card_validation_state(c) for c in cards]
    active = [r for r in rows if r["active"]]
    unresolved = [r for r in active if not r["resolved"]]
    contradictory = [r for r in active if r["reason"] in _CONTRADICTIONS]
    offerable = [
        r for r, c in zip(rows, cards, strict=True)
        if r["active"] and is_compatible(c, PLATFORM_BALANCE_CURRENCY)
    ]
    problems: list[str] = []
    if unresolved:
        problems.append(
            f"{len(unresolved)} active card(s) cannot be resolved: "
            + ", ".join(f"••{r['last4']} ({r['reason']})" for r in unresolved)
        )
    if contradictory:
        problems.append(
            f"{len(contradictory)} active card(s) contradict themselves: "
            + ", ".join(f"••{r['last4']}" for r in contradictory)
        )
    if not offerable:
        problems.append(
            f"no active card can receive a {PLATFORM_BALANCE_CURRENCY} transfer — "
            f"the top-up screen would have nowhere to send money"
        )
    return {
        "wallet_currency": PLATFORM_BALANCE_CURRENCY,
        "cards": rows,
        "active": len(active),
        "unresolved": len(unresolved),
        "contradictory": len(contradictory),
        "offerable": [r["id"] for r in offerable],
        "problems": problems,
        "ok": not problems,
    }


def _print_table(verdict: dict[str, Any]) -> None:
    print(f"platform wallet currency: {verdict['wallet_currency']}")
    if not verdict["cards"]:
        print("  (no receiving cards configured)")
    header = f"  {'id':<14} {'last4':<6} {'network':<11} {'settles':<8} {'active':<7} state"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for r in verdict["cards"]:
        state = "OK" if r["resolved"] else f"UNRESOLVED — {_REASON_HELP.get(r['reason'], r['reason'])}"
        source = f" [{r['source']}]" if r["resolved"] and r["source"] else ""
        print(f"  {r['id'][:14]:<14} ••{r['last4']:<4} {r['network'] or '-':<11} "
              f"{r['settlement_currency'] or '-':<8} {str(r['active']).lower():<7} {state}{source}")
    print(f"\n  offerable for {verdict['wallet_currency']}: {len(verdict['offerable'])} "
          f"of {verdict['active']} active")
    for problem in verdict["problems"]:
        print(f"  FAIL: {problem}")
    if verdict["ok"]:
        print("  PASS: every active card resolves and the wallet currency is receivable")


def main() -> int:
    try:
        cards = _load_cards()
    except Exception as exc:                                  # an operator tool reports, never crashes
        print(f"could not read {BILLING_CARDS_KEY}: {exc}", file=sys.stderr)
        return 2
    verdict = evaluate(cards)
    if "--json" in sys.argv:
        print(json.dumps(verdict, indent=2, sort_keys=True))
    else:
        _print_table(verdict)
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
