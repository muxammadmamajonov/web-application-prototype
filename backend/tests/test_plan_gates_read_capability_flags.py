"""A paid capability is gated by the plan's FLAG, never by its code.

THE BUG THIS PINS (found by sweeping for hardcoded lists that shadow live
catalogues, 2026-08-09). Plan codes stopped being a fixed enum in R36 — they are
owner-chosen slugs, and the catalogue an operator actually edits moved on to
starter/business/pro/enterprise/flex. Any gate written as
``plan_code in ("pro", "custom")`` therefore encodes a plan list that the
product no longer has, and drifts silently every time HQ adds or renames a plan.

The custom-PDF-template endpoint did exactly that. It even loaded the plan and
read ``allow_pdf_template_upload`` first — then ``pass``ed, discarding the
answer, and gated on the code tuple instead. Measured against the live plans
table at the time of the fix:

    starter     allow=False   code-gate denied    correct by luck
    business    allow=True    code-gate DENIED    wrong — plan grants it
    pro         allow=True    code-gate allowed   correct
    enterprise  allow=True    code-gate DENIED    wrong — the top tier
    flex        allow=False   code-gate denied    correct by luck

So two paying tiers, one of them the highest, were refused a capability their
own plan record granted, and an operator toggling that flag in the plan editor
changed nothing. ``custom`` had not existed for some time, so that arm of the
tuple matched nothing at all.

Nothing here can be caught by a type checker or by any test that does not know
what rows the plans table holds, which is why it survived. These assertions are
deliberately about the SHAPE of the gate: a capability check that names a plan
is the defect, whatever the names happen to be today.
"""
from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

# The plan codes that have appeared in this product. A gate comparing against
# ANY of them is keying on identity instead of capability — including the ones
# that are currently correct, because "currently correct" is what the two broken
# arms above also looked like right up until the catalogue changed.
#
# `pro` belongs here even though it is a live, currently-correct plan: the
# regex below captures the FIRST literal of a tuple, and the real gate opened
# with ("pro", "custom"). Leaving it out let this test pass against the very
# code it exists to reject — caught by running it against the pre-fix source.
KNOWN_PLAN_CODES = ("lite", "pro", "custom", "starter", "business", "enterprise", "flex")

# `trial` is deliberately absent: it is a documented built-in sentinel for the
# pre-subscription lifecycle ("the target plan must exist, or be the built-in
# trial"), not a catalogue row, and provisioning validates every other code
# against the plans table.


def _python_sources() -> list[tuple[Path, str]]:
    return [(p, p.read_text(encoding="utf-8")) for p in APP.rglob("*.py")]


def test_the_sweep_reaches_the_source() -> None:
    """Guards the assertions below from vacuously passing over an empty list."""
    assert len(_python_sources()) > 40


def test_no_capability_gate_compares_plan_code_to_a_literal() -> None:
    """`plan_code == "pro"` / `plan_code not in (...)` must not decide access."""
    # Comparisons of plan_code against a string literal, in either direction.
    pattern = re.compile(
        r"plan_code\s*(?:==|!=|\s+in\s+|\s+not\s+in\s+)\s*\(?\s*[\"']([a-z_]+)[\"']"
    )
    offenders: list[str] = []
    for path, text in _python_sources():
        for i, line in enumerate(text.splitlines(), start=1):
            # Prose about the old gate is not the old gate. Skipping comments
            # keeps a fix free to explain precisely what it replaced — which is
            # the most useful thing such a comment can do.
            if line.lstrip().startswith("#"):
                continue
            m = pattern.search(line)
            if not m or m.group(1) not in KNOWN_PLAN_CODES:
                continue
            offenders.append(f"{path.relative_to(APP.parent)}:{i}  {line.strip()[:96]}")

    assert not offenders, (
        "these decide access by plan NAME rather than by the plan's capability "
        "flag:\n" + "\n".join(offenders) + "\n\n"
        "Plan codes are owner-chosen slugs (R36) and the catalogue changes "
        "underneath any literal list. Load the Plan row and read the flag that "
        "describes the capability — that is what an operator actually sets."
    )


def test_the_custom_pdf_gate_reads_the_flag() -> None:
    """The specific endpoint the bug was found in, pinned by name."""
    src = (APP / "api" / "pdf.py").read_text(encoding="utf-8")
    assert "allow_pdf_template_upload" in src, (
        "the custom-PDF gate no longer mentions its capability flag"
    )
    # The flag must be DECIDING, not merely computed and dropped — which is
    # precisely what the original code did before falling through to the code
    # tuple. A bare `pass` in the gate is that shape.
    assert "plan.allow_pdf_template_upload" in src, (
        "the flag must be read off the plan and acted on, not just looked up"
    )
