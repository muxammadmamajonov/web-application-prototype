"""SEC-003 — the high-cost / high-abuse routes carry a per-identity rate limit.

Scope is deliberately bounded to the audit's named set. This is NOT a blanket
limiter over all 190 routes: a low global ceiling would throttle normal
multi-widget UI fan-out, and there is no traffic evidence to justify one.

Two things are asserted:
  * the limiter is actually WIRED to each named route (a route-to-limiter matrix,
    so moving a route cannot silently drop its protection); and
  * the configured ceiling blocks a burst while leaving a realistic screen load
    far below the threshold.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import app.core.ratelimit as rl
from app.main import app

# ── the bounded target set ──────────────────────────────────────────────────
# (method, path, expected limiter bucket)
EXPENSIVE_ROUTES: list[tuple[str, str, str]] = [
    # aggregate report reads — each one scans and groups
    ("GET", "/api/tenant/reports/aging", "report"),
    ("GET", "/api/tenant/reports/cashflow", "report"),
    ("GET", "/api/tenant/reports/staff-performance", "report"),
    ("GET", "/api/tenant/reports/products", "report"),
    ("GET", "/api/tenant/reports/ltv", "report"),
    ("GET", "/api/tenant/reports/margin", "report"),
    # PDF renders — WeasyPrint CPU per request
    ("GET", "/api/tenant/installments/{installment_id}/contract.pdf", "pdf"),
    ("GET", "/api/tenant/payments/{payment_id}/receipt.pdf", "pdf"),
    ("GET", "/api/tenant/invoices/{invoice_id}/invoice.pdf", "pdf"),
    ("GET", "/api/tenant/settings/contract-template/preview.pdf", "pdf"),
    # bulk import parsing / mutation
    ("POST", "/api/tenant/import/preview", "import"),
    ("POST", "/api/tenant/products/import", "import"),
    ("POST", "/api/tenant/customers/import", "import"),

    # ── amplification + external cost (cache/ratelimit audit, 2026-08-12) ──
    # The set above bounds work WE pay for in CPU. This set bounds work that
    # leaves the building — a message to a real person, bytes into storage, a
    # dump of somebody's record. None of these carried any limit at all, so a
    # loop against an authenticated session could use the product to message a
    # shop's entire customer base, fill its object storage, or drain every
    # customer record it can reach.
    ("POST", "/api/tenant/broadcasts", "broadcast"),
    ("POST", "/api/platform/broadcasts", "broadcast"),
    ("POST", "/api/tenant/invitations", "invite"),
    ("POST", "/api/tenant/customers/{cid}/invite", "invite"),
    ("POST", "/api/platform/admin-invitations", "invite"),
    ("POST", "/api/platform/tenants/{tenant_id}/owner-invite", "invite"),
    ("POST", "/api/tenant/upload-image", "upload"),
    ("POST", "/api/tenant/upload-kyc", "upload"),
    ("POST", "/api/catalog/upload-image", "upload"),
    ("GET", "/api/tenant/customers/{cid}/export", "export"),
]


def _dep_buckets(method: str, path: str) -> set[str]:
    """Rate-limit buckets wired to a route, read off the live app."""
    buckets: set[str] = set()
    for route in app.routes:
        if getattr(route, "path", None) != path:
            continue
        if method not in getattr(route, "methods", set()):
            continue
        for dep in getattr(route, "dependant", None).dependencies:
            call = getattr(dep, "call", None)
            bucket = getattr(call, "_ratelimit_bucket", None)
            if bucket:
                buckets.add(bucket)
    return buckets


@pytest.mark.parametrize("method,path,bucket", EXPENSIVE_ROUTES)
def test_expensive_route_is_rate_limited(method: str, path: str, bucket: str) -> None:
    """Route-to-limiter matrix: each named route carries its expected bucket."""
    assert path in {getattr(r, "path", None) for r in app.routes}, f"route missing: {path}"
    assert bucket in _dep_buckets(method, path), (
        f"{method} {path} has no '{bucket}' rate limit wired"
    )


def test_matrix_covers_every_named_family() -> None:
    """The matrix itself must not silently shrink.

    Per-family counts rather than one total, so removing a route from one family
    cannot be masked by adding one to another.
    """
    expected = {
        # cost to US: CPU, scans, parsing
        "report": 6, "pdf": 4, "import": 3,
        # cost OUTSIDE: messages sent, bytes stored, records dumped
        "broadcast": 2, "invite": 4, "upload": 3, "export": 1,
    }
    actual = {b: len([r for r in EXPENSIVE_ROUTES if r[2] == b]) for b in expected}
    assert actual == expected
    assert len(EXPENSIVE_ROUTES) == sum(expected.values())


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    async def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, key, ttl):
        return True


class _Req:
    def __init__(self, user: str = "u-1", ip: str = "1.2.3.4") -> None:
        from app.core.security import issue_token
        self.headers = {"authorization": f"Bearer {issue_token(user_id=user)}"}
        self.client = type("C", (), {"host": ip})()


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("factory_name,ceiling,normal_burst", [
    ("report_limit", 60, 12),   # a reports screen mounts ~6 widgets; 12 = two full loads
    ("pdf_limit", 30, 3),       # user-initiated, one document at a time
    ("import_limit", 10, 2),    # bulk upload is rare and deliberate
])
def test_ceiling_blocks_burst_but_allows_normal_use(
    monkeypatch, factory_name: str, ceiling: int, normal_burst: int
) -> None:
    """Normal UI fan-out stays well under the ceiling; a real burst is refused."""
    fake = _FakeRedis()
    monkeypatch.setattr(rl, "get_redis", lambda: fake)
    dep = getattr(rl, factory_name)().dependency
    req = _Req()

    async def run():
        for _ in range(normal_burst):
            await dep(req)                      # realistic screen load: never blocked
        for _ in range(ceiling - normal_burst):
            await dep(req)                      # up to the ceiling: still allowed
        with pytest.raises(HTTPException) as ei:
            await dep(req)                      # one past the ceiling: refused
        assert ei.value.status_code == 429
        assert ei.value.detail == "rate_limited"
        assert "Retry-After" in ei.value.headers

    _run(run())


def test_limits_are_per_identity_not_one_global_bucket(monkeypatch) -> None:
    """One noisy tenant user must not consume another user's allowance."""
    fake = _FakeRedis()
    monkeypatch.setattr(rl, "get_redis", lambda: fake)
    dep = rl.report_limit().dependency

    async def run():
        noisy = _Req(user="u-noisy")
        for _ in range(60):
            await dep(noisy)
        with pytest.raises(HTTPException):
            await dep(noisy)                    # noisy user is now limited
        await dep(_Req(user="u-quiet"))         # a different identity is unaffected

    _run(run())


def test_expensive_limits_fail_open_when_redis_is_down(monkeypatch) -> None:
    """Availability policy: an app-use limiter must not lock out the product on a
    Redis blip. Only the auth/signup buckets fail closed, and that is unchanged."""
    class _Dead:
        async def incr(self, key): raise ConnectionError("redis down")
        async def expire(self, key, ttl): raise ConnectionError("redis down")

    monkeypatch.setattr(rl, "get_redis", lambda: _Dead())
    for name in ("report_limit", "pdf_limit", "import_limit", "search_limit"):
        dep = getattr(rl, name)().dependency
        _run(dep(_Req()))  # returns without raising


def test_existing_stricter_controls_are_untouched() -> None:
    """The pre-existing auth/signup fail-closed policy must survive this change."""
    assert rl.login_limit().dependency._ratelimit_fail_closed is True
    assert rl.signup_limit().dependency._ratelimit_fail_closed is True
    assert rl.report_limit().dependency._ratelimit_fail_closed is False


# ── conditional search limiting ─────────────────────────────────────────────
SEARCH_ROUTES = [("GET", "/api/tenant/customers"), ("GET", "/api/tenant/installments")]


@pytest.mark.parametrize("method,path", SEARCH_ROUTES)
def test_search_routes_carry_the_conditional_limiter(method: str, path: str) -> None:
    assert "search" in _dep_buckets(method, path), f"{method} {path} lacks the search bucket"


class _QReq(_Req):
    def __init__(self, q: str | None, user: str = "u-s") -> None:
        super().__init__(user=user)
        self.query_params = {"q": q} if q is not None else {}


def test_unfiltered_list_paging_is_never_limited(monkeypatch) -> None:
    """Paging a plain list is cheap — it must not consume the search allowance."""
    fake = _FakeRedis()
    monkeypatch.setattr(rl, "get_redis", lambda: fake)
    dep = rl.search_limit_if_query().dependency

    async def run():
        for _ in range(500):          # far beyond the search ceiling
            await dep(_QReq(None))
        assert fake.store == {}        # nothing was even counted

    _run(run())


def test_blank_query_is_treated_as_unfiltered(monkeypatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(rl, "get_redis", lambda: fake)
    dep = rl.search_limit_if_query().dependency
    _run(dep(_QReq("   ")))
    assert fake.store == {}


def test_text_search_is_limited_above_the_ceiling(monkeypatch) -> None:
    """A real `q=` search scans, so it is counted — with type-ahead headroom."""
    fake = _FakeRedis()
    monkeypatch.setattr(rl, "get_redis", lambda: fake)
    dep = rl.search_limit_if_query().dependency

    async def run():
        for _ in range(90):
            await dep(_QReq("ali"))
        with pytest.raises(HTTPException) as ei:
            await dep(_QReq("ali"))
        assert ei.value.status_code == 429

    _run(run())
