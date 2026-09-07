"""Application source may not carry finding or phase identifiers.

An audit finding is a moment in a review; the code outlives it. `# (FB-021)` on
a line tells a future reader nothing they can act on — it points at a document
they do not have — while a comment stating the invariant ("a payment is
denominated in its contract's currency") is useful forever.

Regression tests and audit documents are exactly where those identifiers DO
belong: a test named after the defect it pins is how you find the reason a rule
exists. This guard therefore covers application source only, and is deliberately
repository-wide rather than diff-scoped, so the boundary holds for code nobody
is currently editing.
"""
from __future__ import annotations

import pathlib
import re

TRIPLE = chr(34) * 3

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]

#: Where application source lives. `backend/tests`, `frontend/**/*.test.*` and
#: `docs/` are intentionally absent.
SOURCE_ROOTS = (
    ("backend/app", ("*.py",)),
    ("backend/alembic", ("*.py",)),
    ("backend/scripts", ("*.py",)),
    ("frontend/src", ("*.ts", "*.tsx", "*.css")),
)

#: `FB-021`, `Phase 9`, `Phase-2`, `PHASE 10`.
FORBIDDEN = re.compile(r"\bFB-\d{3}\b|\bPhase[- ]\d+\b", re.IGNORECASE)

#: Narration about what the code used to do — a comment describing a diff
#: rather than an invariant. Enforced only over the modules this work
#: introduced: the wider codebase carries ~30 such comments predating it, and
#: rewriting those is its own change, not a side effect of this one.
HISTORY = re.compile(
    r"\bTHE DEFECT\b|\bthis used to\b|\bit used to\b"
    r"|\bused to (?:be|read|do|say|render|return|sit|call|carry|check|offer|print)\b"
    r"|\bthe (?:old|previous|original|former) (?:guard|version|policy|code|behaviour|behavior|"
    r"implementation|fixture|comment|list|approach|design)\b"
    r"|\bwas reported\b|\bthe founder\b|\bfounder beta\b|\bREPRODUCED\b"
    r"|\bthis replaces\b|\bwhat went wrong\b|\bthe defect this\b"
    r"|\bpreviously (?:printed|stored|accepted|granted|returned|matched|sent)\b",
    re.IGNORECASE,
)

#: Prose length. An invariant fits in a few lines; an essay does not. Applied
#: to the modules listed below rather than to every greenfield entry: the
#: earlier ones predate the rule and rewriting them is a separate change.
MAX_DOCSTRING_LINES = 24
LENGTH_CHECKED = (
    "backend/app/core/documents.py",
    "backend/app/core/upload_guard.py",
    "backend/app/core/review_intent.py",
    "backend/app/services/document_delivery.py",
    "backend/app/services/payment_review_notify.py",
    "backend/app/models/document_delivery.py",
    "backend/app/models/payment_review_notice.py",
    "backend/app/api/review_intents.py",
)

#: Modules written from scratch for the currency / top-up / wizard work. They
#: are wholly ours, so they are held to the stricter prose rule too.
GREENFIELD = (
    "backend/app/core/currency.py",
    "backend/app/core/card_networks.py",
    "backend/alembic/versions/ra0_contract_payment_currency.py",
    "backend/scripts/check_billing_cards.py",
    "frontend/src/components/ui/DestinationCardRow.tsx",
    "frontend/src/components/ui/StepForm.tsx",
    "frontend/src/lib/cardNetworks.ts",
    # Document delivery / upload security / review actions.
    "backend/app/core/documents.py",
    "backend/app/core/upload_guard.py",
    "backend/app/core/review_intent.py",
    "backend/app/services/document_delivery.py",
    "backend/app/services/payment_review_notify.py",
    "backend/app/models/document_delivery.py",
    "backend/app/models/payment_review_notice.py",
    "backend/app/api/review_intents.py",
    "backend/alembic/versions/re0_document_deliveries.py",
    "backend/alembic/versions/re1_payment_review_notices.py",
    "frontend/src/routes/tenant/PaymentReviewPage.tsx",
    "frontend/src/routes/ReviewIntentPage.tsx",
    "frontend/src/lib/useDocumentSend.ts",
)


def _sources() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for root, patterns in SOURCE_ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        for pattern in patterns:
            out += [
                p for p in base.rglob(pattern)
                # A test living beside its subject is still a test.
                if ".test." not in p.name and "__pycache__" not in p.parts
                and "node_modules" not in p.parts
            ]
    return sorted(out)


def _hits(pattern: re.Pattern[str]) -> list[str]:
    found: list[str] = []
    for path in _sources():
        for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if pattern.search(line):
                found.append(f"{path.relative_to(REPO)}:{n}  {line.strip()[:100]}")
    return found


def test_the_guard_can_actually_see_the_source() -> None:
    """A path typo would make every assertion below vacuously true."""
    files = _sources()
    assert len(files) > 200, len(files)
    names = {p.name for p in files}
    assert {"currency.py", "card_networks.py", "StepForm.tsx"} <= names


def test_no_finding_or_phase_identifiers_in_application_source() -> None:
    hits = _hits(FORBIDDEN)
    assert hits == [], (
        "Finding/phase identifiers belong in the audit report and in test "
        "names, not in shipped code. State the invariant instead:\n  "
        + "\n  ".join(hits)
    )


def test_no_implementation_history_narrated_in_the_new_modules() -> None:
    hits: list[str] = []
    for rel in GREENFIELD:
        path = REPO / rel
        assert path.is_file(), f"{rel} moved — update GREENFIELD or the guard is blind"
        for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if HISTORY.search(line):
                hits.append(f"{rel}:{n}  {line.strip()[:100]}")
    assert hits == [], (
        "A comment explaining what the code used to do describes a diff. Say "
        "what must remain true instead:\n  " + "\n  ".join(hits)
    )


@pytest.mark.parametrize(
    "sample",
    ["# (FB-021) snapshot", "// FB-003 selection", "/* Phase 9 gate */",
     "# Phase-2 decision", "* THE DEFECT. It exploded.", "// this used to be a form"],
)
def test_the_patterns_match_what_they_claim_to(sample) -> None:
    """The guard is only worth having if it would actually fire."""
    assert FORBIDDEN.search(sample) or HISTORY.search(sample), sample


@pytest.mark.parametrize(
    "sample",
    [
        "# A payment is denominated in its contract's currency.",
        "# Uzcard's 5614 range sits inside Mastercard's 51-55.",
        "revision = 'ra0currency'",
        "# (#134) Reject negative per-unit prices.",
        "# Round 39 override",
    ],
)
def test_durable_comments_are_not_flagged(sample) -> None:
    """It must not push people into deleting the comments that matter."""
    assert not FORBIDDEN.search(sample) and not HISTORY.search(sample), sample


def test_greenfield_module_docstrings_state_invariants_not_essays() -> None:
    """A docstring that runs for pages is an audit report in the wrong file."""
    long_ones: list[str] = []
    for rel in LENGTH_CHECKED:
        path = REPO / rel
        if not path.is_file() or path.suffix in (".ts", ".tsx"):
            continue
        text = path.read_text(errors="replace").lstrip()
        if not text.startswith(TRIPLE):
            continue
        body = text[3:]
        end = body.find(TRIPLE)
        if end == -1:
            continue
        lines = body[:end].splitlines()
        if len(lines) > MAX_DOCSTRING_LINES:
            long_ones.append(f"{rel}: {len(lines)} lines (limit {MAX_DOCSTRING_LINES})")
    assert not long_ones, (
        "module docstrings have grown into audit prose:\n  " + "\n  ".join(long_ones)
        + "\n\nKeep the invariant; move the reasoning to the audit report."
    )
