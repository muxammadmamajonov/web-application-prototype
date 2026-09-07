"""Every route's database authority, inventoried statically (Batch 5, Part 5).

This walks the live route table and asserts that each surface carries the
dependency that grants — or deliberately withholds — its database authority. It
is a contract test, not the boundary: the boundary is the SQL policy, and a
route that slips past this check still reads nothing without a scope. What this
buys is a fast, readable failure at the moment somebody adds an endpoint to the
wrong package, instead of a silent empty list in production.

The rule it encodes is one sentence: **authority is granted by the surface, and
the default surface grants none.**
"""
from __future__ import annotations

from fastapi.routing import APIRoute

#: prefix → the dependency every route under it must declare.
#:
#: Two surfaces are deliberately ABSENT from this table and present in
#: ``FORBIDDEN_AUTHORITY`` below instead. Both used to be here, and both were
#: wrong (see the independent-review correction to Batch 5):
#:
#: ``/api/webhooks``  carried ``worker_authority`` at the router, so an
#:                    UNAUTHENTICATED provider callback held cross-tenant reach
#:                    before its signature was checked;
#: ``/api/auth``      carried ``identity_authority``, whose ``users`` policy was
#:                    ``USING (true)``, so every handler under it — including
#:                    ones added later — could read and write the whole user
#:                    directory before a credential had been checked.
#:
#: Both now grant nothing at the router and take a narrow, per-operation
#: capability after the request has proved something.
REQUIRED_AUTHORITY: tuple[tuple[str, str], ...] = (
    ("/api/platform", "platform_authority"),
    ("/api/me", "customer_authority"),
    ("/api/bot", "worker_authority"),
    ("/api/admin/cron", "worker_authority"),
)

#: prefix → dependencies no route under it may declare, and why.
FORBIDDEN_AUTHORITY: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "/api/webhooks",
        ("worker_authority", "platform_authority", "identity_authority"),
        "a public provider callback is unauthenticated until its signature is "
        "verified; it resolves one provider through a narrow capability and "
        "narrows to the verified tenant afterwards",
    ),
    (
        "/api/auth",
        ("worker_authority", "platform_authority", "identity_authority"),
        "the login surface takes the identity authority per OPERATION, for a "
        "single capability call, and that authority has no policy on `users`",
    ),
)

#: Tenant back-office surfaces resolve a tenant per route (the header decides
#: WHICH tenant, so it cannot be hoisted to the router).
TENANT_PREFIXES: tuple[str, ...] = (
    "/api/tenant/", "/api/reports/", "/api/sms/",
)

#: Routes under a tenant prefix that legitimately do NOT resolve a tenant.
#: Each one is a capability or a public surface, and each narrows its own reach
#: through ``scope_to_object`` or ``invite_codes.unlock`` instead. Listing them
#: by name is the point: adding a route here is a review decision, not a default.
TENANT_PREFIX_EXCEPTIONS: frozenset[str] = frozenset({
    # Invitation credentials are presented by people who are not yet members of
    # anything; the raw credential is exchanged for a scope inside the handler.
    "get_invitation_context", "invitation_identity", "claim_invitation",
    # The two PDF routes identify the CALLER but not a shop: a customer reaches
    # their own contract without being a member of anything. Authorization is
    # `_can_access_installment`, which narrows the request to the installment's
    # own tenant via `scope_to_object` before it reads a row.
    "installment_contract_pdf", "payment_receipt_pdf",
})


def _dependency_names(route: APIRoute) -> set[str]:
    """Every dependency callable reachable from ``route``, by name."""
    seen: set[str] = set()

    def walk(dep) -> None:
        if dep.call is not None:
            seen.add(getattr(dep.call, "__name__", ""))
        for sub in dep.dependencies:
            walk(sub)

    walk(route.dependant)
    return seen


def _under(path: str, prefix: str) -> bool:
    """Prefix match on whole path SEGMENTS.

    ``str.startswith`` would put ``/api/media/{id}`` under ``/api/me`` and demand
    the customer authority of a signed capability route — which is how the first
    run of this test failed.
    """
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


def _routes() -> list[APIRoute]:
    from app.main import app

    return [r for r in app.routes if isinstance(r, APIRoute)]


def test_every_privileged_surface_declares_its_authority() -> None:
    missing: list[str] = []
    for route in _routes():
        for prefix, required in REQUIRED_AUTHORITY:
            if _under(route.path, prefix):
                if required not in _dependency_names(route):
                    missing.append(f"{route.path} ({route.name}) lacks {required}")
                break
    assert not missing, (
        "route(s) on a privileged surface with no database authority dependency:\n  "
        + "\n  ".join(missing)
    )


def test_no_public_surface_inherits_an_authority_from_its_router() -> None:
    """The other half of the same rule, and the one that was violated.

    "Authority is granted by the surface" cuts both ways: a surface that has not
    authenticated anybody must grant NOTHING, and it must do so at the router,
    where a future endpoint added to the same package inherits the absence
    rather than the grant.
    """
    offences: list[str] = []
    checked = 0
    for route in _routes():
        for prefix, forbidden, why in FORBIDDEN_AUTHORITY:
            if not _under(route.path, prefix):
                continue
            checked += 1
            held = _dependency_names(route) & set(forbidden)
            if held:
                offences.append(
                    f"{route.path} ({route.name}) holds {sorted(held)} — {why}",
                )
            break
    assert checked >= 5, "the unauthenticated surfaces were not found"
    assert not offences, (
        "unauthenticated route(s) hold a database authority at the door:\n  "
        + "\n  ".join(offences)
    )


def test_every_tenant_route_resolves_a_tenant() -> None:
    """A tenant back-office route that forgets ``resolve_tenant`` gets no scope.

    Under the Batch 5 policies that is a fail-closed empty result rather than a
    cross-tenant disclosure — ``test_rls_matrix`` proves that separately — but a
    route that reads nothing is still a bug, and this is where it is cheapest to
    notice.
    """
    missing: list[str] = []
    for route in _routes():
        if not any(_under(route.path, p) for p in TENANT_PREFIXES):
            continue
        if route.name in TENANT_PREFIX_EXCEPTIONS:
            continue
        if "resolve_tenant" not in _dependency_names(route):
            missing.append(f"{route.path} ({route.name})")
    assert not missing, (
        "tenant route(s) with no resolve_tenant dependency — add it, or classify "
        "the route in TENANT_PREFIX_EXCEPTIONS with the reason:\n  "
        + "\n  ".join(missing)
    )


def test_no_ordinary_route_grants_itself_worker_or_platform_authority() -> None:
    """Elevation is confined to the surfaces that are allowed to have it."""
    allowed = {p for p, _ in REQUIRED_AUTHORITY}
    offenders: list[str] = []
    for route in _routes():
        names = _dependency_names(route)
        elevated = names & {"worker_authority", "platform_authority"}
        if not elevated:
            continue
        if not any(_under(route.path, p) for p in allowed):
            offenders.append(f"{route.path} ({route.name}) -> {sorted(elevated)}")
    assert not offenders, (
        "route(s) outside the privileged surfaces claim an elevated authority:\n  "
        + "\n  ".join(offenders)
    )


def test_the_customer_surface_never_claims_more_than_customer_authority() -> None:
    for route in _routes():
        if not _under(route.path, "/api/me"):
            continue
        names = _dependency_names(route)
        assert "worker_authority" not in names, route.path
        assert "platform_authority" not in names, route.path


def test_capability_routes_narrow_themselves_to_one_object() -> None:
    """The unauthenticated signed surfaces resolve an object before reading it.

    ``/dl``, ``/receipts`` and ``/media`` are authorized by an HMAC over one
    object id and carry no session at all, so there is no tenant to resolve —
    they call ``scope_to_object`` instead, which narrows the request to that
    object's shop. Asserted on the source because there is no dependency to
    inspect: the call is inside the handler, after the signature check.
    """
    import inspect

    from app.api import downloads, media, receipts

    for module, fn in (
        (downloads, "serve_download"), (downloads, "grant_download"),
        (receipts, "serve_receipt"), (media, "serve_media"),
    ):
        src = inspect.getsource(getattr(module, fn))
        assert "scope_to_object" in src, (
            f"{module.__name__}.{fn} reads a tenant-owned object without narrowing "
            "the request to its tenant first"
        )
