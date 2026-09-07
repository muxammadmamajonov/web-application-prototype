"""#74 — public claim-body validation + rate-limit wiring.

Pure-unit (no DB / no live Redis), matching the style of
test_security_fixes.py and test_payment_webhooks.py:

  * The public ``POST /inv/{token}/claim`` body is now bound to the declared
    ``ClaimCustomerIn`` / ``ClaimStaffIn`` models (selected by ``inv.kind``)
    instead of raw ``body.get(...)`` — an over-sized / malformed / wrong-type
    body is rejected with 422 before any side effect runs.
  * The claim endpoint, the invitation-context GET (token-probing), and the
    Payme / Click / subscription webhook POSTs all carry a rate-limit
    dependency (the ``rate_limit`` closure) on their route.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException, status
from fastapi.routing import APIRoute

from app.api import invitations as inv
from app.api.invitations import (
    _MAX_EXTRA_FIELDS,
    _MAX_EXTRA_VALUE_LEN,
    ClaimCustomerIn,
    ClaimStaffIn,
    _parse_claim_body,
)
from app.api.webhooks import click, payme, subscription

# Marker the rate_limit() factory stamps onto its returned closure.
_RL_QUALNAME = "rate_limit.<locals>.dependency"


# ── (a) body is validated against the kind-discriminated models ────────────

def _valid_customer() -> dict:
    return {"init_data": "x", "full_name": "Ali Valiyev", "phone": "+998901112233"}


def _valid_staff() -> dict:
    return {"init_data": "x", "first_name": "Ali", "phone": "+998901112233"}


def test_kind_selects_the_right_model() -> None:
    assert isinstance(_parse_claim_body("customer", _valid_customer()), ClaimCustomerIn)
    # Guarantor shares the customer shape (+ optional ``relationship``).
    g = _parse_claim_body(
        "guarantor", {**_valid_customer(), "relationship": "brother"},
    )
    assert isinstance(g, ClaimCustomerIn)
    assert g.relationship == "brother"
    # Staff and platform_admin both key off the Telegram account only.
    assert isinstance(_parse_claim_body("staff", _valid_staff()), ClaimStaffIn)
    assert isinstance(_parse_claim_body("platform_admin", _valid_staff()), ClaimStaffIn)


@pytest.mark.parametrize(
    ("kind", "body", "label"),
    [
        ("customer", {"init_data": "x", "full_name": "A", "phone": "+998901112233"}, "full_name too short"),
        # A customer body with no phone is no longer a SCHEMA violation: a person
        # whose number Telegram has already proved must not be asked to retype it.
        # The requirement moved to the handler, which is the only place that knows
        # whether a proof exists — asserted with real initData in
        # test_fb012_invitation_identity.py::test_a_claim_with_neither_proof_nor_typed_phone_is_refused.
        ("staff", {"init_data": "x", "phone": "+998901112233"}, "staff missing first_name"),
        # Narrowed metadata bag: count- and size-bounded, str→str only.
        ("customer", {**_valid_customer(), "extra": {str(i): "v" for i in range(_MAX_EXTRA_FIELDS + 1)}}, "extra over field cap"),
        ("customer", {**_valid_customer(), "extra": {"k": "z" * (_MAX_EXTRA_VALUE_LEN + 1)}}, "extra value over length cap"),
        ("customer", {**_valid_customer(), "extra": [1, 2, 3]}, "extra wrong container type"),
        ("customer", {**_valid_customer(), "extra": {"k": {"nested": "obj"}}}, "extra rejects nested objects"),
    ],
)
def test_invalid_or_oversized_body_is_422(kind: str, body: dict, label: str) -> None:
    with pytest.raises(HTTPException) as ei:
        _parse_claim_body(kind, body)
    assert ei.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY, label


def test_extra_within_bounds_is_accepted() -> None:
    claim = _parse_claim_body(
        "customer",
        {**_valid_customer(), "extra": {"shoe_size": "42", "ref": "friend"}},
    )
    assert isinstance(claim, ClaimCustomerIn)
    assert claim.extra == {"shoe_size": "42", "ref": "friend"}


# ── (b) rate-limit dependency is wired on the public + webhook routes ───────

def _route_dep_qualnames(router, path: str, method: str) -> list[str]:
    for r in router.routes:
        if isinstance(r, APIRoute) and r.path == path and method in r.methods:
            return [d.call.__qualname__ for d in r.dependant.dependencies]
    raise AssertionError(f"route not found: {method} {path}")


@pytest.mark.parametrize(
    ("router", "path", "method"),
    [
        (inv.router, "/inv/{token}/claim", "POST"),   # public, side-effectful claim
        (inv.router, "/inv/{token}", "GET"),          # token-probing oracle
        (payme.router, "/payme", "POST"),             # per-tenant Payme JSON-RPC
        (click.router, "/click/prepare", "POST"),
        (click.router, "/click/complete", "POST"),
        (subscription.router, "/subscription/payme", "POST"),
        (subscription.router, "/subscription/click", "POST"),
    ],
)
def test_route_has_rate_limit_dependency(router, path: str, method: str) -> None:
    assert _RL_QUALNAME in _route_dep_qualnames(router, path, method)
