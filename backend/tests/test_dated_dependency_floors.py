"""Dated dependency deferrals fail once their date passes.

`pyproject.toml` criticises itself for a deferral that was two days overdue
while a known-vulnerable dependency served production traffic, and concludes
that such a decision needs an alarm rather than only a comment. This is that
alarm.

Each entry is a floor we deliberately did NOT raise, plus the date the reason
for waiting expires. After that date the test fails, and the only ways to make
it pass are to raise the floor or to write down a new decision.
"""
from __future__ import annotations

import tomllib
from datetime import UTC, date, datetime
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

#: package → (current floor, the date the deferral expires, why it was deferred)
#:
#: EMPTY, and that is the intended resting state. An entry here is a known
#: vulnerable dependency serving production traffic on a promise, and the whole
#: point of the table is that the promise has a deadline the suite enforces.
#:
#: The last entry was ``pypdf``, deferred to 2026-08-13 while 6.15.0 sat inside
#: the 7-day ``exclude-newer`` cooldown. The alarm fired on the day, the floor
#: was raised to ``>=6.15.0,<7``, and the entry was REMOVED — not re-dated.
#: Moving the date is the one repair this table exists to prevent.
DEFERRED_FLOORS: dict[str, tuple[str, date, str]] = {}


def _declared_specifier(package: str) -> str | None:
    data = tomllib.loads(PYPROJECT.read_text())
    for raw in data["project"]["dependencies"]:
        name = raw.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip()
        if name == package:
            return raw[len(name):].strip()
    return None


def test_no_deferral_is_expired() -> None:
    """Every deferral is still inside its window, and the table is honest.

    ONE test, looping, rather than two parametrized over the table. Parametrizing
    an empty dict collects zero cases and pytest reports them as SKIPPED — an
    alarm that reports "skipped" the moment it has nothing to guard, and would
    report the same if somebody mistyped a package name while adding a row back.
    A loop always executes, so this module cannot be green because it ran
    nothing.

    Both properties are checked together because they fail together: a floor
    raised in `pyproject.toml` without removing the row here leaves a stale
    decision outliving the thing it described, which is how the last one lived
    two days past its date.
    """
    today = datetime.now(UTC).date()
    problems: list[str] = []
    for package, (floor, expires, why) in sorted(DEFERRED_FLOORS.items()):
        if today >= expires:
            problems.append(
                f"{package} {floor} was deferred until {expires} and today is "
                f"{today}.\n  Reason given: {why}\n  Raise the floor in "
                "pyproject.toml, re-lock, re-run the dependency audit, and "
                "REMOVE this row. Do not move the date."
            )
        declared = _declared_specifier(package)
        if declared != floor:
            problems.append(
                f"{package}'s pyproject specifier is {declared!r} but this table "
                f"says {floor!r}. If the floor was raised, remove this row."
            )
    assert not problems, "\n".join(problems)


# ── the floor is not a promise; the runtime is what actually loads ───────────
#
# Raising a specifier in `pyproject.toml` proves nothing on its own: the lock
# decides what is installed, and the interpreter decides what is imported. These
# three assertions close the gap between the three, which is exactly where the
# pypdf deferral lived for a week.

def _locked_version(package: str) -> str | None:
    """The version `uv.lock` pins for ``package``."""
    lock = tomllib.loads(
        (PYPROJECT.parent / "uv.lock").read_text(),
    )
    for entry in lock.get("package", []):
        if entry.get("name") == package:
            return entry.get("version")
    return None


def test_the_runtime_imports_the_locked_pypdf() -> None:
    """Declared floor ≤ locked version == imported version.

    All three have to agree. A raised floor with a stale lock installs the old
    release; a fresh lock with an unsynced virtualenv imports the old release;
    and either way the advisory is still live while the repository claims it is
    closed.
    """
    import pypdf
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

    declared = _declared_specifier("pypdf")
    locked = _locked_version("pypdf")
    imported = pypdf.__version__

    assert declared == ">=6.15.0,<7", (
        f"pypdf's declared floor is {declared!r}; PYSEC-2026-3655 and "
        "PYSEC-2026-3656 are fixed in 6.15.0"
    )
    assert locked is not None, "pypdf is not in uv.lock"
    assert Version(locked) in SpecifierSet(declared), (
        f"uv.lock pins pypdf {locked}, which does not satisfy {declared} — re-lock"
    )
    assert imported == locked, (
        f"the interpreter imported pypdf {imported} but uv.lock pins {locked} — "
        "run `uv sync --locked --dev`"
    )
    assert Version(imported) >= Version("6.15.0")


def test_the_upload_guard_exercises_the_locked_pypdf() -> None:
    """And it is actually USED, not merely importable.

    A version assertion on an unused dependency is theatre. This drives the real
    PDF validation path — the one every uploaded document takes — so the test
    fails if the upgrade broke parsing as well as if it never happened.
    """
    import pypdf

    from app.core.upload_guard import pdf_page_count, validate_pdf
    from tests._pdf_fixtures import classic_pdf, objstm_pdf

    # Both container shapes, because they take different pypdf code paths: a
    # classic cross-reference table and an object-stream PDF, which is the one a
    # regex-based validator cannot read and pypdf can.
    for data in (classic_pdf(pages=2), objstm_pdf(pages=3)):
        validate_pdf(data)                # raises UploadRejected on a bad file
        assert pdf_page_count(data) >= 2
    assert pypdf.__version__ == _locked_version("pypdf")
