"""TR-005 — the platform API's route contract, pinned in registration order.

``app/api/platform`` was split from one module into a package. Registration
order is part of the contract, so this test compares an ordered sequence index
by index — no sorting, no sets, no self-updating snapshot. Each domain module
owns a contiguous run of this sequence, which is what lets several files
reproduce it exactly rather than approximately.

The span assertion is the app-wide half: these 42 routes must still occupy the
same contiguous block of the application's route table, starting at the same
index, so a reorder inside the package cannot be masked by counting alone.
"""
from __future__ import annotations

from fastapi.routing import APIRoute

#: Index of the first /api/platform route in the application's route table.
#: 72 -> 71: the removed `GET /demo/intent/{slug}` was registered upstream of
#: this block, so every index below it shifted by one. The block itself is
#: unchanged — still contiguous, still in the same order.
#: 71 -> 73: #10 registered `downloads.router` ahead of `media`, which sits
#: before the platform block, so every platform route shifted by two. The
#: offset moved; what this test actually guards did not — the block is still
#: contiguous and still in the same internal order, which is what would break
#: if a router were reordered or a route silently dropped.
# 74 since GET /contact/channels was added to the contact router, which is
# registered ahead of the platform package — the block itself is unchanged
# and still contiguous, it simply starts one slot later.
#: Shifted 74 -> 76: two identity routes register ahead of this block
#: (POST /api/inv/{token}/identity, GET /api/me/identity).
# 77 since Batch 4: `review_intents` is registered next to `downloads`, which
# sits ahead of the platform block, so the whole span moved down by one.
# The test's job is to catch an ACCIDENTAL reordering — a deliberate
# addition updates the offset rather than being routed around it.
PLATFORM_SPAN_START = 77

#: Total APIRoutes registered by the application.
#: 194 -> 193: `GET /demo/intent/{slug}` was removed with the self-serve demo
#: provisioning it existed to serve (see app/core/scope.py).
#: 193 -> 195: `POST /api/downloads/grant` and `GET /api/dl/{kind}/{ident}`
#: were added by #10 — the signed-download pair that lets a Telegram WebView
#: open a contract PDF at all. `/dl` is deliberately UNAUTHENTICATED: its HMAC
#: signature is the capability, which is exactly why it is worth counting here.
# 196 since GET /contact/channels: the support handle and corporate
# email are configured on Sozlamalar -> Aloqa kanali, and nothing read
# them back — every user-facing mention was a frontend constant, so the
# settings form was a set of fields whose values went nowhere.
# 200 -> 202: MUDD-134 added the owner-dashboard analytics pair
# (GET /api/tenant/analytics/summary, GET /api/tenant/analytics/sales-by-category),
# registered inside tenant.router — after the platform block, so PLATFORM_SPAN_START
# is unaffected; only the app-wide total moves.
TOTAL_APP_ROUTES = 202

#: Every /api/platform route, in registration order, as
#: (method, path, name, status_code, response_model, tags, dependency count,
#:  response_model_by_alias, operation_id, deprecated).
#: Batch 5: the dependency count moved 0 -> 1 on every route. The platform
#: package now carries `Depends(platform_authority)` at the ROUTER, which
#: verifies the platform role and grants the cross-tenant database authority
#: in one place — so an endpoint added to this package cannot be shipped
#: without it, and an endpoint added anywhere else cannot acquire it.
PLATFORM_ROUTE_SEQUENCE: tuple[tuple, ...] = (
    ('GET', '/api/platform/broadcasts', 'list_platform_broadcasts', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('POST', '/api/platform/broadcasts', 'create_platform_broadcast', 201, "<class 'dict'>", ('platform',), 2, True, None, None),
    ('GET', '/api/platform/users/segments', 'list_user_segments', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('GET', '/api/platform/users', 'list_platform_users', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('GET', '/api/platform/overview', 'overview', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('GET', '/api/platform/payments', 'platform_payments', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('POST', '/api/platform/tenants', 'create_tenant', 201, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('POST', '/api/platform/tenants/{tenant_id}/owner-invite', 'regenerate_owner_invite', 201, "<class 'dict'>", ('platform',), 2, True, None, None),
    ('DELETE', '/api/platform/tenants/{tenant_id}', 'delete_tenant', 200, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('GET', '/api/platform/tenants/{tenant_id}/detail', 'tenant_detail', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('POST', '/api/platform/tenants/{tenant_id}/balance', 'topup_balance', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('GET', '/api/platform/topups', 'list_topups', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('POST', '/api/platform/topups/{topup_id}/approve', 'approve_topup', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('POST', '/api/platform/topups/{topup_id}/reject', 'reject_topup', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('POST', '/api/platform/tenants/{tenant_id}/set-plan', 'set_tenant_plan', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('PUT', '/api/platform/tenants/{tenant_id}/plan-overrides', 'set_plan_overrides', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('PATCH', '/api/platform/tenants/{tenant_id}/status', 'set_tenant_status', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('POST', '/api/platform/admin-invitations', 'create_admin_invitation', 201, "<class 'dict'>", ('platform',), 2, True, None, None),
    ('PATCH', '/api/platform/plans/{code}', 'update_plan', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('POST', '/api/platform/plans', 'create_plan', 201, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('DELETE', '/api/platform/plans/{code}', 'delete_plan', 200, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('PUT', '/api/platform/settings/{key}', 'put_setting', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('GET', '/api/platform/tenants', 'list_tenants', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('GET', '/api/platform/plans', 'list_plans', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('GET', '/api/platform/admins', 'list_admins', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('PATCH', '/api/platform/admins/{user_id}', 'update_admin', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('DELETE', '/api/platform/admins/{user_id}', 'remove_admin', 200, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('GET', '/api/platform/audit', 'list_audit', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('GET', '/api/platform/promos', 'list_promos', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('POST', '/api/platform/promos', 'create_promo', 201, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('PATCH', '/api/platform/promos/{promo_id}', 'update_promo', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('DELETE', '/api/platform/promos/{promo_id}', 'delete_promo', 200, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('GET', '/api/platform/ads', 'list_ads', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('POST', '/api/platform/ads', 'create_ad', 201, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('PATCH', '/api/platform/ads/{ad_id}', 'update_ad', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('DELETE', '/api/platform/ads/{ad_id}', 'delete_ad', 200, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('GET', '/api/platform/referrals', 'list_referrals', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('GET', '/api/platform/referrals/my-link', 'my_referral_link', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('POST', '/api/platform/referrals', 'create_referral', 201, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('DELETE', '/api/platform/referrals/{referral_id}', 'delete_referral', 204, None, ('platform',), 1, True, None, None),
    ('GET', '/api/platform/settings', 'list_settings', None, "<class 'dict'>", ('platform',), 1, True, None, None),
    ('GET', '/api/platform/risk/stats', 'risk_network_stats', None, "<class 'dict'>", ('platform',), 1, True, None, None),
)


def _api_routes() -> list[APIRoute]:
    from app.main import app
    return [r for r in app.routes if isinstance(r, APIRoute)]


def _row(r: APIRoute) -> tuple:
    return (
        sorted(r.methods)[0], r.path, r.name, r.status_code,
        repr(r.response_model) if r.response_model else None,
        tuple(r.tags or []), len(r.dependencies or []),
        r.response_model_by_alias, r.operation_id, r.deprecated,
    )


def test_platform_routes_are_registered_in_the_expected_order() -> None:
    """The whole /api/platform contract, index by index."""
    live = [_row(r) for r in _api_routes() if r.path.startswith("/api/platform")]

    assert len(live) == len(PLATFORM_ROUTE_SEQUENCE), (
        f"route count changed: {len(PLATFORM_ROUTE_SEQUENCE)} -> {len(live)}"
    )
    for i, (expected, actual) in enumerate(zip(PLATFORM_ROUTE_SEQUENCE, live, strict=True)):
        assert actual == expected, (
            f"route {i} changed:\n  expected {expected}\n  actual   {actual}"
        )
    assert live == list(PLATFORM_ROUTE_SEQUENCE)


def test_platform_routes_occupy_the_same_span_of_the_app_route_table() -> None:
    """A reorder inside the package must not shift the block, or the app around it."""
    routes = _api_routes()
    assert len(routes) == TOTAL_APP_ROUTES

    idx = [i for i, r in enumerate(routes) if r.path.startswith("/api/platform")]
    assert idx == list(range(PLATFORM_SPAN_START, PLATFORM_SPAN_START + len(PLATFORM_ROUTE_SEQUENCE))), (
        "the platform routes are no longer one contiguous block at the original offset"
    )


def test_no_platform_route_is_registered_twice() -> None:
    live = [(r[0], r[1]) for r in (_row(x) for x in _api_routes()
                                   if x.path.startswith("/api/platform"))]
    assert len(live) == len(set(live)), "a duplicate (method, path) appeared"
