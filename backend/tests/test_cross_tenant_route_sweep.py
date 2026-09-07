"""Every id-taking tenant route, swept for cross-tenant reach and role reach (MUDD-139).

Two shops go live on shared infrastructure at the same time, so the two questions
this file answers are the two that matter most:

1. **Isolation.** Shop A's owner, presenting shop A's tenant header, pointed at
   shop B's object ids. Nothing may succeed and nothing may echo B's data back.
2. **Role reach.** A ``sales`` member against every owner/manager surface. The
   UI hides these controls; this asserts the API refuses them too, which is the
   only half an attacker cannot skip.
3. **The id-taking routes that are NOT under ``/api/tenant/``** — the customer's
   own ``/api/me/...`` views and the two blob routes. They carry no tenant
   header at all (the caller is a person, not a shop), so their scoping comes
   from the identity alone and is worth its own probe.

WHY A TABLE AND NOT ONE TEST PER ROUTE
--------------------------------------
A route added next week is a route nobody remembers to write an isolation test
for. Driving the whole set from one list means adding a row is the cheap thing to
do, and ``test_every_id_route_is_swept`` below fails if the app grows an
id-taking tenant route that this table does not mention — so the list cannot
silently fall behind the router.

A NOTE ON 422
-------------
FastAPI validates the body BEFORE the endpoint runs, so a probe with a malformed
payload gets 422 without ever reaching the tenant or role guard — it proves
nothing while looking like a refusal. Every body below is therefore valid enough
to reach the guard, and the assertions reject 422 explicitly rather than
accepting it as "denied".
"""
from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests import _dbcreds

pytestmark = pytest.mark.asyncio(loop_scope="module")

_ITEM = {"product_name_snapshot": "probe", "qty": 1, "unit_price": "100000"}

#: ``(method, path template, body)``. ``{...}`` placeholders are filled with the
#: OTHER shop's ids by the isolation test.
CROSS_TENANT_CASES: tuple[tuple[str, str, dict | None], ...] = (
    ("GET",    "/api/tenant/customers/{cid}", None),
    ("PATCH",  "/api/tenant/customers/{cid}", {"full_name": "pwn"}),
    ("DELETE", "/api/tenant/customers/{cid}", None),
    ("POST",   "/api/tenant/customers/{cid}/approve", None),
    ("POST",   "/api/tenant/customers/{cid}/reject", None),
    ("GET",    "/api/tenant/customers/{cid}/channels", None),
    ("PUT",    "/api/tenant/customers/{cid}/channels", {"channels": []}),
    ("POST",   "/api/tenant/customers/{cid}/erase", None),
    ("GET",    "/api/tenant/customers/{cid}/export", None),
    ("POST",   "/api/tenant/customers/{cid}/invite", None),
    ("GET",    "/api/tenant/installments/{iid}", None),
    ("PUT",    "/api/tenant/installments/{iid}",
     {"customer_id": "{own_cid}", "months": 6, "items": [_ITEM]}),
    ("POST",   "/api/tenant/installments/{iid}/cancel", {"reason": "probe"}),
    ("POST",   "/api/tenant/installments/{iid}/remind", None),
    ("POST",   "/api/tenant/installments/{iid}/reschedule", {"add_months": 1}),
    ("GET",    "/api/tenant/installments/{iid}/contract.pdf", None),
    ("POST",   "/api/tenant/payments/{pid}/approve", None),
    ("POST",   "/api/tenant/payments/{pid}/reject", {"reason": "probe"}),
    ("GET",    "/api/tenant/payments/{pid}/receipt.pdf", None),
    ("GET",    "/api/tenant/payments/{pid}/review", None),
    ("DELETE", "/api/tenant/invitations/{inv}", None),
    ("GET",    "/api/tenant/products/{prod}", None),
    ("PATCH",  "/api/tenant/products/{prod}", {"name_uz": "pwn"}),
    ("DELETE", "/api/tenant/products/{prod}", None),
    ("GET",    "/api/tenant/products/{prod}/movements", None),
    ("POST",   "/api/tenant/products/{prod}/stock-adjust", {"qty": 1, "kind": "in", "reason": "probe"}),
    ("PATCH",  "/api/tenant/guarantors/{gua}", {"full_name": "pwn"}),
    ("DELETE", "/api/tenant/guarantors/{gua}", None),
    ("DELETE", "/api/tenant/broadcasts/{bcast}", None),
    ("PATCH",  "/api/tenant/staff/{staff}", {"is_active": False}),
    ("DELETE", "/api/tenant/staff/{staff}", None),
    ("GET",    "/api/tenant/invoices/{inv_pdf}/invoice.pdf", None),
)

#: Surfaces the tenant UI shows only to an owner or a manager. A ``sales``
#: member must be refused by the API, not merely by the screen.
SALES_MUST_BE_REFUSED: tuple[tuple[str, str, dict | None], ...] = (
    ("GET",    "/api/tenant/settings", None),
    ("PATCH",  "/api/tenant/settings", {"locale": "uz"}),
    ("GET",    "/api/tenant/staff", None),
    ("GET",    "/api/tenant/billing", None),
    ("GET",    "/api/tenant/invoices", None),
    ("GET",    "/api/tenant/billing/topup-info", None),
    ("GET",    "/api/tenant/billing/topups", None),
    ("POST",   "/api/tenant/plan-change", {"plan_code": "pro"}),
    ("GET",    "/api/tenant/customers/{own_cid}/export", None),
    ("POST",   "/api/tenant/customers/{own_cid}/erase", None),
    ("DELETE", "/api/tenant/customers/{own_cid}", None),
    ("GET",    "/api/tenant/kyc-media?ref={own_cid}", None),
    ("GET",    "/api/tenant/risk/score?customer_id={own_cid}", None),
    ("POST",   "/api/tenant/risk/report", {"customer_id": "{own_cid}"}),
    ("POST",   "/api/tenant/payments/{own_pid}/approve", None),
    ("POST",   "/api/tenant/payments/{own_pid}/reject", {"reason": "x"}),
    ("GET",    "/api/tenant/payments/{own_pid}/review", None),
    ("POST",   "/api/tenant/installments/{own_iid}/cancel", {"reason": "x"}),
    ("POST",   "/api/tenant/broadcasts", {"title": "t", "body_uz": "b"}),
    ("POST",   "/api/tenant/products", {"name_uz": "p", "base_price": "1000"}),
)

#: Owner-only even for a manager — the narrower of the two staff guards. This is
#: the pair `BillingPage.tsx` documents ("the backend still enforces the rule").
MANAGER_MUST_BE_REFUSED: tuple[tuple[str, str, dict | None], ...] = (
    ("POST",   "/api/tenant/plan-change", {"plan_code": "pro"}),
    ("GET",    "/api/tenant/customers/{own_cid}/export", None),
    ("POST",   "/api/tenant/customers/{own_cid}/erase", None),
)


#: Id-taking routes outside ``/api/tenant/``. Driven as a person who has no
#: relationship to the object: shop A's verified customer against shop B's
#: installment/payment, and shop B's owner against shop A's media/receipt blob.
FOREIGN_PERSON_CASES: tuple[tuple[str, str, str, dict | None], ...] = (
    ("customer_a", "GET",  "/api/me/installments/{iid_b}", None),
    ("customer_a", "GET",  "/api/me/installments/{iid_b}/payment-methods", None),
    ("customer_a", "GET",  "/api/me/installments/{iid_b}/payoff-quote", None),
    ("customer_a", "POST", "/api/me/installments/{iid_b}/payoff", {"method": "cash"}),
    ("customer_a", "GET",  "/api/me/payments/{pid_b}/status", None),
    ("owner_b",    "GET",  "/api/media/{media_a}", None),
    ("owner_b",    "GET",  "/api/receipts/{receipt_a}", None),
)


@pytest_asyncio.fixture(loop_scope="module")
async def swept(rls_world, monkeypatch):
    from sqlalchemy import select

    import app.db as db
    from app.config import settings
    from app.db import get_db
    from app.main import app
    from app.models import TenantUser, User
    from app.models.enums import TenantRole

    Seed = async_sessionmaker(rls_world.engine, expire_on_commit=False)
    ids: dict[str, str] = {}
    async with Seed() as s:
        # Idempotent — this fixture is function-scoped (it needs `monkeypatch`)
        # but `rls_world` is module-scoped, so it re-enters against one database.
        for tag, role, tg in (("sales", TenantRole.sales, 970000501),
                              ("manager", TenantRole.manager, 970000502)):
            u = (await s.execute(
                select(User).where(User.telegram_id == tg)
            )).scalar_one_or_none()
            if u is None:
                u = User(telegram_id=tg, first_name=f"{tag} A")
                s.add(u)
                await s.flush()
                s.add(TenantUser(tenant_id=rls_world.tenant_a, user_id=u.id,
                                 role=role, is_active=True))
            ids[tag] = str(u.id)

        # Shop B objects for the id-taking routes `rls_world` does not seed.
        # Real rows, not random UUIDs: a 404 for "no such row" would pass this
        # sweep while proving nothing about isolation.
        #
        # Each lookup names its own uniqueness key rather than sharing a generic
        # one — `invoices` is unique on (tenant_id, period_start), so a generic
        # "match the second kwarg" probe re-inserted it every entry.
        from datetime import date

        from app.models import Broadcast, Guarantor, Invoice, Product

        async def _ensure(model, where, **kwargs):
            row = (await s.execute(select(model).where(*where))).scalars().first()
            if row is None:
                row = model(**kwargs)
                s.add(row)
                await s.flush()
            return str(row.id)

        tb = rls_world.tenant_b
        ids["prod"] = await _ensure(
            Product, (Product.tenant_id == tb, Product.name_uz == "B product"),
            tenant_id=tb, name_uz="B product")
        ids["gua"] = await _ensure(
            Guarantor, (Guarantor.tenant_id == tb, Guarantor.phone == "+998900000199"),
            tenant_id=tb, full_name="B guarantor", phone="+998900000199")
        ids["bcast"] = await _ensure(
            Broadcast, (Broadcast.tenant_id == tb, Broadcast.title == "B blast"),
            tenant_id=tb, title="B blast", body_uz="x")
        period = date(2026, 1, 1)
        ids["inv_pdf"] = await _ensure(
            Invoice, (Invoice.tenant_id == tb, Invoice.period_start == period),
            tenant_id=tb, plan_code="pro", period_start=period,
            period_end=date(2026, 1, 31), issued_at=period, due_at=date(2026, 1, 15))

        # A staff membership in shop B, to point PATCH/DELETE /staff at.
        staff_b = (await s.execute(
            select(TenantUser).where(TenantUser.tenant_id == rls_world.tenant_b,
                                     TenantUser.user_id == rls_world.owner_b)
        )).scalars().first()
        ids["staff"] = str(staff_b.id)
        await s.commit()

    engine = create_async_engine(_dbcreds.render(rls_world.engine.url),
                                 pool_size=5, max_overflow=0)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(settings, "rls_enabled", True)

    async def _override():
        async with Session() as s:
            try:
                yield s
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_db] = _override
    db.reset_db_scope()
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app),
                                     base_url="http://t") as client:
            yield client, rls_world, ids
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.reset_db_scope()
        await engine.dispose()


def _auth(user_id: str) -> dict[str, str]:
    from app.core.security import issue_token
    return {"Authorization": f"Bearer {issue_token(user_id=user_id)}"}


def _fill(value: str, subs: dict[str, str]) -> str:
    for k, v in subs.items():
        value = value.replace("{" + k + "}", v)
    return value


async def test_no_tenant_route_reaches_the_other_shop(swept):
    """21 id-taking routes × shop B's ids → every one refused, none echo B."""
    import json as _json

    import app.db as db

    client, w, ids = swept
    headers = {**_auth(w.owner_a), "X-Tenant-Id": w.tenant_a}
    subs = {"cid": w.customer_b, "iid": w.installment_b, "pid": w.payment_b,
            "inv": w.invitation_b, "own_cid": w.customer_a,
            "prod": ids["prod"], "gua": ids["gua"], "bcast": ids["bcast"],
            "staff": ids["staff"], "inv_pdf": ids["inv_pdf"]}

    leaks: list[str] = []
    for method, template, body in CROSS_TENANT_CASES:
        payload = None
        if body is not None:
            payload = _json.loads(_fill(_json.dumps(body), subs))
        db.reset_db_scope()
        r = await client.request(method, _fill(template, subs),
                                 headers=headers, json=payload)
        if r.status_code < 400:
            leaks.append(f"{method} {template} -> {r.status_code} {r.text[:120]}")
        elif r.status_code == 422:
            # Body rejected before the guard ran — the probe proved nothing.
            leaks.append(f"{method} {template} -> 422 (never reached the guard)")
        elif "Bob" in r.text:          # shop B's customer name
            leaks.append(f"{method} {template} echoed shop B data: {r.text[:120]}")
    assert not leaks, "cross-tenant reach:\n  " + "\n  ".join(leaks)


async def test_downloads_grant_will_not_sign_another_shops_contract(swept):
    """The signed URL is a capability — it must never be minted across tenants."""
    import app.db as db
    client, w, _ = swept
    db.reset_db_scope()
    r = await client.post(
        "/api/downloads/grant",
        headers={**_auth(w.owner_a), "X-Tenant-Id": w.tenant_a},
        json={"kind": "contract", "id": w.installment_b},
    )
    assert r.status_code >= 400, r.text


@pytest.mark.parametrize("role,cases", [
    ("sales", SALES_MUST_BE_REFUSED),
    ("manager", MANAGER_MUST_BE_REFUSED),
])
async def test_role_cannot_reach_a_surface_the_ui_hides(swept, role, cases):
    import json as _json

    import app.db as db

    client, w, ids = swept
    headers = {**_auth(ids[role]), "X-Tenant-Id": w.tenant_a}
    subs = {"own_cid": w.customer_a, "own_pid": w.payment_a,
            "own_iid": w.installment_a}

    allowed: list[str] = []
    for method, template, body in cases:
        payload = None
        if body is not None:
            payload = _json.loads(_fill(_json.dumps(body), subs))
        db.reset_db_scope()
        r = await client.request(method, _fill(template, subs),
                                 headers=headers, json=payload)
        if r.status_code != 403:
            allowed.append(f"{method} {template} -> {r.status_code} {r.text[:120]}")
    assert not allowed, f"{role} was not refused:\n  " + "\n  ".join(allowed)


async def test_topup_is_owner_only_for_everyone_below_owner(swept):
    """`POST /billing/topup` takes multipart, so it needs its own probe."""
    import app.db as db
    client, w, ids = swept
    for role in ("sales", "manager"):
        db.reset_db_scope()
        r = await client.post(
            "/api/tenant/billing/topup",
            headers={**_auth(ids[role]), "X-Tenant-Id": w.tenant_a},
            data={"amount": "100000", "method": "card"},
        )
        assert r.status_code == 403, f"{role}: {r.status_code} {r.text[:160]}"
        assert r.json()["detail"] == "owner_only"


def test_every_id_route_is_swept() -> None:
    """The table above may not fall behind the router.

    Any ``/api/tenant/...`` route carrying a path parameter must appear in
    ``CROSS_TENANT_CASES``, so adding a route forces a decision about its
    isolation rather than letting it ship unswept.
    """
    from app.main import app

    swept_pairs = {(m, t.split("?")[0]) for m, t, _ in CROSS_TENANT_CASES}
    # Normalise the router's own parameter names onto this file's short ones.
    # ORDER MATTERS. The router calls the product id `{pid}` and this file calls
    # the PAYMENT id `{pid}`, so `{pid}` -> `{prod}` must run BEFORE
    # `{payment_id}` -> `{pid}`; the other order rewrites payment routes twice
    # ({payment_id} -> {pid} -> {prod}) and silently reports them as unswept.
    rename = (("{pid}", "{prod}"), ("{payment_id}", "{pid}"),
              ("{installment_id}", "{iid}"), ("{invite_id}", "{inv}"),
              ("{gid}", "{gua}"), ("{bid}", "{bcast}"),
              ("{tu_id}", "{staff}"), ("{invoice_id}", "{inv_pdf}"))

    missing = []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None)
        if not methods or not path.startswith("/api/tenant/") or "{" not in path:
            continue
        normalised = path
        for src, dst in rename:
            normalised = normalised.replace(src, dst)
        for method in methods - {"HEAD", "OPTIONS"}:
            if (method, normalised) not in swept_pairs:
                missing.append(f"{method} {path}")
    assert not missing, (
        "id-taking tenant routes with no cross-tenant probe — add them to "
        "CROSS_TENANT_CASES:\n  " + "\n  ".join(sorted(missing))
    )


async def test_a_person_cannot_reach_an_object_they_have_no_relation_to(swept):
    """The ``/api/me/...`` and blob routes carry no tenant header — identity is
    the only scope they have, so each is probed with a caller who has none."""
    import app.db as db

    client, w, _ = swept
    subs = {"iid_b": w.installment_b, "pid_b": w.payment_b,
            "media_a": w.media_a, "receipt_a": w.receipt_a}
    actors = {"customer_a": w.customer_user, "owner_b": w.owner_b}

    reached: list[str] = []
    for actor, method, template, body in FOREIGN_PERSON_CASES:
        path = _fill(template, subs)
        db.reset_db_scope()
        r = await client.request(method, path, headers=_auth(actors[actor]),
                                 json=body)
        if r.status_code < 400 or r.status_code == 422:
            reached.append(f"{actor}: {method} {template} -> {r.status_code} "
                           f"{r.text[:120]}")
    assert not reached, "a stranger reached an object:\n  " + "\n  ".join(reached)
