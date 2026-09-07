"""Golden-vector parity test for the installment-schedule calculation.

The schedule math lives in two hand-synced places — this backend
``app/services/calc.py`` and the frontend ``src/lib/calc.ts`` (which calls
itself a "Client-side mirror"). It is a money path (markup/annuity/
differentiated formulas), so silent drift between the two would quote the
customer one number and charge another.

This test and its frontend twin (``frontend/src/lib/calc.parity.test.ts``)
both load the SAME committed fixture, ``calc-golden-vectors.json`` at the repo
root, and assert their implementation reproduces it exactly. Any change to
either side that diverges from the fixture fails CI on that side.

The fixture is parsed with ``parse_float=Decimal`` so a rate like ``0.025``
arrives as ``Decimal("0.025")`` (exact) rather than a binary-contaminated
float — matching how the real call path feeds Decimals into ``compute_schedule``.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.calc import compute_schedule

# backend/tests/test_calc_parity.py -> parents[2] is the repo root.
_FIXTURE = Path(__file__).resolve().parents[2] / "calc-golden-vectors.json"


def _load_cases() -> list[dict]:
    with _FIXTURE.open(encoding="utf-8") as fh:
        data = json.load(fh, parse_float=Decimal)
    cases = data["cases"]
    assert cases, "golden fixture has no cases"
    return cases


_CASES = _load_cases()


def _ids(case: dict) -> str:
    return str(case["name"])


def test_fixture_covers_all_four_formulas() -> None:
    formulas = {c["input"]["formula"] for c in _CASES}
    assert {"none", "markup", "annuity", "differentiated"} <= formulas
    # And at least one deliberate "down payment too high" edge.
    assert any("expectError" in c for c in _CASES)


@pytest.mark.parametrize("case", _CASES, ids=_ids)
def test_backend_matches_golden_vectors(case: dict) -> None:
    inp = case["input"]
    kwargs = dict(
        goods_total=inp["goodsTotal"],
        down_payment=inp.get("downPayment") or 0,
        months=inp["months"],
        formula=inp["formula"],
        markup_pct=inp.get("markupPct"),
        interest_rate=inp.get("interestRate"),
    )

    if "expectError" in case:
        # Backend rejects a non-positive financed amount (the frontend instead
        # returns a zeroed preview — that divergence is asserted on the FE side).
        with pytest.raises(ValueError, match=case["expectError"]):
            compute_schedule(**kwargs)
        return

    r = compute_schedule(**kwargs)
    exp = case["expect"]
    assert int(r.total_amount) == exp["total"]
    assert int(r.financed) == exp["financed"]
    assert int(r.principal) == exp["principal"]
    assert int(r.monthly_amount) == exp["monthly"]
    # Invariant the whole money convention rests on.
    assert r.monthly_amount * r.months == r.principal
