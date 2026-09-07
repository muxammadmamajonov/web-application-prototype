"""No `db.refresh(tenant)` after a commit in a tenant-scoped request.

THE BUG THIS PINS (observed in production 2026-08-06). Saving Business Settings
returned "Temporary server error". The log:

    File "/app/app/api/tenant_settings.py", line 355, in patch_settings
    sqlalchemy.exc.InvalidRequestError: Could not refresh instance '<Tenant>'

`resolve_tenant` loads the Tenant with a plain SELECT *before* `_apply_rls_scope`
pins the request — so that read happens as `postgres`, which bypasses RLS. The
handler then commits. Committing ends the transaction, and `SET LOCAL` state
dies with it; the `after_begin` listener re-pins `app.tenant_id` on the next
transaction, but from that point the session is the restricted `muddato_app`
role. A refresh of the `tenants` row then returns no row, and SQLAlchemy turns
"no row" into `InvalidRequestError`.

WHY NO TEST CAUGHT IT. The suite runs with `rls_enabled` off, so every one of
these refreshes succeeds locally and fails only in production. That asymmetry is
the whole reason this guard is a SOURCE check rather than a behavioural one:
a behavioural test would need RLS wired into the test database to fail, and
would pass green here for exactly the wrong reason.

WHY THE REFRESH WAS POINTLESS ANYWAY. `SessionLocal` is
`expire_on_commit=False` (app/db.py), so committing does not expire anything —
the in-memory `tenant` still holds every value the handler set. The refresh
bought nothing and cost a 500.

Platform and onboarding paths are exempt: they never call `resolve_tenant`, so
the contextvar stays unset, the `after_begin` listener is a no-op, and their
work runs as `postgres` (see the fail-open contract in app/db.py).
"""
from __future__ import annotations

import pathlib
import re

API = pathlib.Path(__file__).resolve().parents[1] / "app" / "api"

#: Modules that serve a request AFTER `resolve_tenant` has pinned the RLS scope.
#: Anything under `app/api/tenant/` plus the tenant-settings router.
def _tenant_scoped_files() -> list[pathlib.Path]:
    files = sorted((API / "tenant").rglob("*.py"))
    files.append(API / "tenant_settings.py")
    return [f for f in files if f.is_file()]


#: `db.refresh(tenant)` WITHOUT `with_for_update` — the plain re-read. The
#: locking variant is fine and is used deliberately (billing takes a row lock
#: before debiting a balance); it runs BEFORE the commit, inside the pinned
#: transaction.
PLAIN_TENANT_REFRESH = re.compile(r"\brefresh\(\s*tenant\s*\)")


def test_no_plain_tenant_refresh_in_a_tenant_scoped_request():
    offenders: list[str] = []
    for path in _tenant_scoped_files():
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue          # the explanatory comments left at the fix sites
            if PLAIN_TENANT_REFRESH.search(line):
                offenders.append(f"{path.relative_to(API.parent.parent)}:{i}  {line.strip()}")

    assert not offenders, (
        "these re-read the `tenants` row inside a tenant-scoped request:\n"
        + "\n".join(offenders)
        + "\n\nUnder RLS the row is invisible to the restricted role once the "
          "request has committed, and SQLAlchemy raises 'Could not refresh "
          "instance'. The session is expire_on_commit=False, so the object "
          "already holds its values — delete the refresh. Use "
          "`refresh(tenant, with_for_update=True)` only BEFORE a commit, to lock."
    )


def test_the_locking_refresh_is_still_allowed():
    """Guards the guard: the FOR UPDATE variant must not be swept up.

    Billing takes a row lock before debiting a prepaid balance; removing that
    would reintroduce a lost update, which is worse than the bug above.
    """
    billing = (API / "tenant" / "billing.py").read_text()
    assert "refresh(tenant, with_for_update=True)" in billing


def test_the_session_still_does_not_expire_on_commit():
    """The fix rests on this. If it ever flips, the values the handlers return
    after a commit would be stale-or-refetched and this whole class reopens."""
    db_src = (API.parent / "db.py").read_text()
    assert "expire_on_commit=False" in db_src
