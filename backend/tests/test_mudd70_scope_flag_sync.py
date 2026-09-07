"""M-3 (MUDD-70) — the two scope flags are independent; a mismatch is not
symmetric.

`SELF_SERVE_PROVISIONING_ENABLED` here and `V2_SURFACES_ENABLED`
(`frontend/src/routes/_scope.ts`) are two hand-maintained constants in two
languages and two deploys. `app/core/scope.py`'s own module docstring already
argues against merging them into one shared artifact — a build-time import
from the frontend into Python would buy nothing and couple the two deploys
for no benefit, since neither is env-driven; flipping either is a code change
plus a deploy either way.

DECISION: keep them separate (do not merge), and add a synchronization
CHECKPOINT instead — this file. A same-process import of the frontend
constant is not possible (different language, different deploy artifact), so
what this can do is fail LOUDLY, by name, the moment this backend flag is
flipped, forcing whoever flips it to read the coupling note below before it
ships — rather than discovering the split in production the way M-3's audit
pass found it.

Why the risk is NOT symmetric (the reasoning behind the decision):

  * frontend/src/routes/router.tsx gates `/:locale/signup` behind
    `V2_SURFACES_ENABLED` — with that flag false, the signup PAGE is
    unreachable from the browser, so a backend-only flip of
    SELF_SERVE_PROVISIONING_ENABLED reopens nothing a customer can reach.
  * The dangerous direction is the other one: flipping ONLY the frontend
    flag makes `/signup` reachable and renderable while this backend flag
    still refuses every submit with 403 self_serve_provisioning_disabled
    (see test_p9m1_self_serve_scope.py's `test_signup_is_disabled`) — a
    real form a visitor can fill out that always fails, which is exactly
    the "half-working surface" M-3 was raised about.

test_p9m1_self_serve_scope.py already pins this flag to False on its own
(`test_the_scope_flag_is_off_so_every_gate_below_is_really_closed`); this
file adds the cross-flag coordination note that test never carried, without
touching that file (parallel lane note, MUDD-70/MUDD-71).
"""
from __future__ import annotations


def test_flipping_this_flag_requires_confirming_the_frontend_flag_too():
    """Fails the instant SELF_SERVE_PROVISIONING_ENABLED goes True.

    That is deliberate: there is no automatic cross-repo check possible here
    (see the module docstring), so the checkpoint is a human one. Before
    changing the assertion below, confirm frontend/src/routes/_scope.ts's
    V2_SURFACES_ENABLED is set the way you intend it to be — true if the
    self-serve funnel should actually be reachable, false if you only meant
    to re-enable it on the backend for e.g. a direct-API integration that
    never renders /signup.
    """
    from app.core.scope import SELF_SERVE_PROVISIONING_ENABLED

    assert SELF_SERVE_PROVISIONING_ENABLED is False, (
        "SELF_SERVE_PROVISIONING_ENABLED is now True. STOP: this alone does "
        "not reopen /signup for anyone — frontend/src/routes/router.tsx "
        "gates that route behind V2_SURFACES_ENABLED "
        "(frontend/src/routes/_scope.ts) independently. If that frontend "
        "flag is NOT also being flipped true in this change, you have "
        "intentionally left the backend more permissive than the reachable "
        "surface, which is the safe direction and fine. If it IS also "
        "flipping, update this assertion to `is True` in the same change so "
        "the two are reviewed together, and update the docstring above."
    )
