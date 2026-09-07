"""Server-side MVP scope.

The backend counterpart of ``frontend/src/routes/_scope.ts``. That flag governs
which *pages* exist; this one governs which *provisioning* an anonymous or
merely-authenticated caller can trigger. They are deliberately two constants in
two languages rather than one shared artifact — a build-time import from the
frontend into Python would buy nothing and couple the two deploys.

Self-serve onboarding (ONB-001/-002) and the trial lifecycle are classified
LATER. A tenant provisioned by that funnel goes on to hold customer passport
data, which is the reason the funnel was deferred in the first place, so it must
not be reachable while the classification stands.

What the gate removes is *reachability*: the provisioning code behind it still
exists, so re-opening the two auth entry points is a flip of this constant rather
than a resurrection.

The demo deep link was the third entry point and is **not** one of them. That
branch — the Redis intent store and the bot's ``/start demo_<slug>`` redemption —
was deleted outright rather than gated, because nothing downstream needed it once
the form became lead capture. Restoring it means restoring code from git history,
not flipping this flag.

The explicit ``: bool`` annotation stops type-checkers narrowing the value to the
literal ``False`` and declaring the guarded branches unreachable.
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, status

#: Self-serve tenant/trial provisioning — ``POST /auth/signup`` and
#: ``POST /auth/provision-trial``. Classified LATER. Operator-created tenants and
#: the owner-claim link are a different path and are **not** governed by this
#: flag.
SELF_SERVE_PROVISIONING_ENABLED: bool = False

#: Stable, non-localised error code. The browser never reaches these routes (the
#: signup page is itself behind the frontend scope flag), so this is an API
#: contract for direct callers, not user-facing copy.
DISABLED_DETAIL = "self_serve_provisioning_disabled"


async def _deny_self_serve_provisioning() -> None:
    if not SELF_SERVE_PROVISIONING_ENABLED:
        raise HTTPException(status.HTTP_403_FORBIDDEN, DISABLED_DETAIL)


def self_serve_provisioning_gate() -> Any:
    """Route dependency that closes a self-serve provisioning entry point.

    Declare it *first* in a route's ``dependencies=[...]`` list — ahead of that
    route's rate limiter, which is retained after it so flipping the flag back on
    restores a fully throttled route. Ordering the limiter first would make the
    refusal depend on Redis: ``signup_limit`` is fail-closed, so it would answer
    503 during a Redis outage and 403 otherwise, and it would spend a round-trip
    per request on a route that refuses unconditionally.

    FastAPI solves route-level dependencies in declaration order, ahead of
    parameter dependencies and ahead of body validation, so a gated route answers
    before it authenticates the caller, parses the payload, verifies a signature
    or touches the database.
    """
    return Depends(_deny_self_serve_provisioning)


__all__ = (
    "DISABLED_DETAIL",
    "SELF_SERVE_PROVISIONING_ENABLED",
    "self_serve_provisioning_gate",
)
