"""QA-R-002 — a configured-but-broken database is a FAILURE, never a skip.

The policy this module enforces
-------------------------------
1. ``TEST_DATABASE_URL`` **absent** → skipping is correct and honest. The
   integration tier simply is not configured on this host.
2. ``TEST_DATABASE_URL`` **present** → setup is *mandatory*. A refused
   connection, an ``InsufficientPrivilegeError``, an invalid schema, a failed
   migration — every setup error must **fail** the run.

Why this matters more than it looks
-----------------------------------
The re-audit caught the real failure mode: pointing ``TEST_DATABASE_URL`` at a
role that could not own ``schema public`` produced ``10 skipped`` and exit code
0. RLS, tenant isolation and every DB-backed authorization test executed
*nothing*, while the summary line still read green. A misconfigured CI role
would silently delete the entire tier this phase depends on, and only ``-rs``
would reveal it.

How the guard works — structurally, not by grepping
---------------------------------------------------
This test parses every module under ``backend/tests`` with :mod:`ast` and looks
for a ``pytest.skip()`` call lexically inside an ``except`` handler whose
guarded ``try`` block is doing database setup. Two independent structural
signals mark a handler as DB-related:

* **DB vocabulary** — the ``try`` body mentions engine/connection/schema
  operations (:data:`_DB_VOCAB`); or
* **broad catch in a DB-tier fixture** — the handler catches ``Exception`` /
  ``BaseException`` / bare, inside a function that also gates on
  ``TEST_DATABASE_URL``.

Because detection walks real ``ast.Try`` and ``ast.Call`` nodes, the string
constants in *this* file are inert data — the guard scans its own source along
with everything else and needs **no self-exemption**. That is asserted
explicitly by :func:`test_guard_scans_its_own_source_without_exemption`.

The explicit ``if not url: pytest.skip("TEST_DATABASE_URL not set …")`` gate is
*outside* any exception handler and is therefore correctly allowed — it is the
honest expression of policy rule 1.
"""
from __future__ import annotations

import ast
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

#: Identifiers that mean "this ``try`` block was setting up a database".
#: Matched against both attribute names (``engine.begin``) and plain calls
#: (``create_async_engine(url)``) anywhere in the guarded body.
_DB_VOCAB = frozenset({
    "create_async_engine", "create_engine", "async_sessionmaker", "sessionmaker",
    "begin", "connect", "execute", "run_sync", "create_all", "drop_all",
    "upgrade", "dispose", "exec_driver_sql",
})

#: Catching one of these is "catch everything", which is what turns a privilege
#: or schema error into an indistinguishable "unreachable".
_BROAD_EXCEPTIONS = frozenset({"Exception", "BaseException"})


def _iter_test_modules() -> list[Path]:
    """Every Python module in the backend test suite — including this one."""
    return sorted(p for p in TESTS_DIR.rglob("*.py") if p.name != "__init__.py")


def _names_in(node: ast.AST) -> set[str]:
    """All identifiers appearing under ``node`` (attribute names and bare names)."""
    found: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute):
            found.add(child.attr)
        elif isinstance(child, ast.Name):
            found.add(child.id)
    return found


def _is_pytest_skip(node: ast.AST) -> bool:
    """``pytest.skip(...)`` or a bare ``skip(...)`` from ``from pytest import skip``."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr == "skip"
    return isinstance(func, ast.Name) and func.id == "skip"


def _skip_calls_in(node: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(node) if _is_pytest_skip(n)]


def _catches_broadly(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:  # bare `except:`
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(t, ast.Name) and t.id in _BROAD_EXCEPTIONS for t in types)


def _functions_gating_on_test_database_url(tree: ast.AST) -> list[ast.AST]:
    """Function bodies that read ``TEST_DATABASE_URL`` — i.e. DB-tier fixtures."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        literals = {
            c.value for c in ast.walk(node)
            if isinstance(c, ast.Constant) and isinstance(c.value, str)
        }
        if any("TEST_DATABASE_URL" in lit for lit in literals):
            out.append(node)
    return out


def find_forbidden_skips(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, reason)`` for every DB-setup skip-in-except in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    db_tier_fn_bodies = _functions_gating_on_test_database_url(tree)
    db_tier_try_nodes = {
        id(n) for fn in db_tier_fn_bodies for n in ast.walk(fn) if isinstance(n, ast.Try)
    }

    forbidden: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        body_names = set().union(*(_names_in(stmt) for stmt in node.body)) if node.body else set()
        touches_db = bool(body_names & _DB_VOCAB)
        in_db_tier_fixture = id(node) in db_tier_try_nodes

        for handler in node.handlers:
            for call in _skip_calls_in(handler):
                if touches_db:
                    forbidden.append((
                        call.lineno,
                        "pytest.skip() inside `except` guarding DB setup "
                        f"({', '.join(sorted(body_names & _DB_VOCAB))})",
                    ))
                elif in_db_tier_fixture and _catches_broadly(handler):
                    forbidden.append((
                        call.lineno,
                        "pytest.skip() inside a broad `except` in a "
                        "TEST_DATABASE_URL-gated fixture",
                    ))
    return forbidden


def scan_suite() -> dict[str, list[tuple[int, str]]]:
    """``{relative_path: [(lineno, reason), …]}`` across the whole suite."""
    report: dict[str, list[tuple[int, str]]] = {}
    for path in _iter_test_modules():
        hits = find_forbidden_skips(path)
        if hits:
            report[str(path.relative_to(TESTS_DIR.parent))] = hits
    return report


# ── the contract ────────────────────────────────────────────────────────────

def test_no_db_setup_error_is_converted_into_a_skip() -> None:
    """A configured-but-broken DB must fail the run, everywhere in the suite.

    This is the permanent guard for QA-R-002. It is DB-free and fast, so it
    runs even when the integration tier is not configured — which is exactly
    when a regression here would otherwise go unnoticed.
    """
    report = scan_suite()
    assert report == {}, (
        "Forbidden DB-tier skip(s) — a configured-but-broken database must FAIL, "
        "not self-skip green:\n"
        + "\n".join(
            f"  {path}:{lineno} — {reason}"
            for path, hits in sorted(report.items())
            for lineno, reason in hits
        )
    )


def test_guard_scans_its_own_source_without_exemption() -> None:
    """The guard is structural, so it can scan itself and stay clean.

    A naive text search for "pytest.skip" would match this module's own prose
    and vocabulary constants, forcing a self-exemption that is exactly the
    escape hatch a future regression would slip through.
    """
    scanned = {p.name for p in _iter_test_modules()}
    assert Path(__file__).name in scanned, "the guard must not exclude itself"
    assert find_forbidden_skips(Path(__file__)) == []


def test_explicit_unset_url_skip_is_allowed_outside_exception_handlers() -> None:
    """Policy rule 1 stays legal: no ``TEST_DATABASE_URL`` → an honest skip.

    Proven positively — the suite really does still contain such gates, so a
    future change that deleted them all could not make this guard vacuous.
    """
    conftest = TESTS_DIR / "conftest.py"
    tree = ast.parse(conftest.read_text(encoding="utf-8"), filename=str(conftest))

    handler_skip_lines = {
        call.lineno
        for node in ast.walk(tree) if isinstance(node, ast.Try)
        for handler in node.handlers
        for call in _skip_calls_in(handler)
    }
    all_skip_lines = {call.lineno for call in _skip_calls_in(tree)}
    top_level_skips = all_skip_lines - handler_skip_lines

    assert top_level_skips, "conftest lost its explicit TEST_DATABASE_URL gate"
    assert find_forbidden_skips(conftest) == []


def test_non_database_except_skips_are_not_flagged() -> None:
    """The WeasyPrint skip is legitimate and must survive.

    ``test_round24.py`` catches ``OSError`` around a PDF render when the native
    pango/cairo libs are absent. That is a genuine host-capability gap, not a
    misconfigured database — and note that a *type*-based rule could not tell
    the two apart, since ``OSError`` is itself a connection-class exception.
    Only the structural rule separates them.
    """
    round24 = TESTS_DIR / "test_round24.py"
    assert _skip_calls_in(ast.parse(round24.read_text(encoding="utf-8"))), (
        "expected test_round24.py to still contain its WeasyPrint skip"
    )
    assert find_forbidden_skips(round24) == []
