"""Phase 3 UX-001 — a walk-in sale must not require a Telegram account.

Before this change `POST /tenant/installments` rejected any customer whose
``telegram_id`` was NULL with a 409 ``customer_not_joined``. That made the
product's core job impossible at the counter: the wizard's inline "new customer"
step creates exactly such a customer, so the shop created a customer and was
then refused a contract for them in the same session.

Phase 2 removed that constraint from scope (MVP-CONTRACT-025). These tests pin
the new behaviour AND the security properties that must survive it — a missing
Telegram link was never an authorisation boundary, and removing it must not
loosen one:

  * ownership     — the customer must still belong to the acting tenant;
  * isolation     — no cross-tenant read or write becomes possible;
  * identity      — customer WebApp access still binds strictly on
                    ``Customer.telegram_id == User.telegram_id``, so linking
                    later grants that customer's own contracts and nothing else.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode


def _init_data(telegram_id: int, *, first_name: str = "Joiner") -> str:
    """A correctly signed Telegram Mini-App initData string for `telegram_id`.

    Read the token from the same Settings object as the claim handler. Local
    tests get the inert conftest default while CI intentionally supplies its own
    inert value; hard-coding either one makes this integration proof depend on
    the runner environment instead of the application contract.
    """
    from app.config import settings

    fields = {
        "user": json.dumps({"id": telegram_id, "first_name": first_name, "username": f"u{telegram_id}"}),
        "auth_date": str(int(time.time())),
        "query_id": "AAH",
    }
    dcs = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(
        b"WebAppData", settings.telegram_bot_token.encode(), hashlib.sha256
    ).digest()
    return urlencode({**fields, "hash": hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()})


async def _make_customer(client, auth, name: str, phone: str, telegram_id: int | None = None) -> str:
    body: dict = {"full_name": name, "phone": phone}
    if telegram_id is not None:
        body["telegram_id"] = telegram_id
    r = await client.post("/api/tenant/customers", headers=auth, json=body)
    assert r.status_code == 201, f"create customer: {r.status_code} {r.text[:300]}"
    return r.json()["id"]


async def _join_via_invite(client, auth, customer_id: str, telegram_id: int, *, name: str, phone: str):
    """Link a Telegram account to an existing customer the way a real joiner
    does: prove the phone through the bot, mint the customer-bound one-time
    invite, then claim it with signed initData.

    The proof is part of the real flow, not scaffolding around it: a customer
    relationship carries contracts, so it is created only against proved
    ownership."""
    from sqlalchemy import select

    from app.db import get_db
    from app.main import app
    from app.models import User
    from app.services.telegram_contact import record_verified_contact

    # The fixture's session factory, not the app engine's: this test runs
    # against an ephemeral schema on its own loop.
    agen = app.dependency_overrides[get_db]()
    s = await agen.__anext__()
    try:
        u = (await s.execute(
            select(User).where(User.telegram_id == telegram_id)
        )).scalar_one_or_none()
        if u is None:
            u = User(telegram_id=telegram_id, first_name=name)
            s.add(u)
            await s.flush()
        if not u.verified_phone:
            assert (await record_verified_contact(
                s, sender_telegram_id=telegram_id, contact_user_id=telegram_id,
                raw_phone=phone,
            )).ok
        await s.commit()
    finally:
        await agen.aclose()

    ri = await client.post(f"/api/tenant/customers/{customer_id}/invite", headers=auth)
    assert ri.status_code == 201, f"mint invite: {ri.status_code} {ri.text[:300]}"
    token = ri.json()["token"]
    rc = await client.post(
        f"/api/inv/{token}/claim",
        json={"init_data": _init_data(telegram_id, first_name=name), "full_name": name, "phone": phone},
    )
    assert rc.status_code == 200, f"claim invite: {rc.status_code} {rc.text[:300]}"
    return rc.json()


async def _issue_contract(client, auth, customer_id: str, *, months: int = 6, price: str = "6000000"):
    return await client.post(
        "/api/tenant/installments", headers=auth,
        json={
            "customer_id": customer_id, "months": months,
            "items": [{"product_name_snapshot": "Phone", "unit_price": price, "qty": 1}],
        },
    )


async def test_contract_issues_for_customer_without_telegram(isolation_ctx) -> None:
    """The core walk-in case: no Telegram account, still a valid contract."""
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}

    cid = await _make_customer(client, auth, "Walk In", "+998901110001")

    # The customer genuinely has no Telegram link.
    rc = await client.get(f"/api/tenant/customers/{cid}", headers=auth)
    assert rc.status_code == 200
    assert rc.json().get("telegram_id") in (None, 0)

    r = await _issue_contract(client, auth, cid)
    assert r.status_code == 201, f"walk-in contract refused: {r.status_code} {r.text[:300]}"
    assert r.json()["total_amount"] == 6000000
    assert "customer_not_joined" not in r.text


async def test_inline_created_customer_is_immediately_usable(isolation_ctx) -> None:
    """The wizard's inline create → select → sell path, end to end, in one go.

    This is the exact sequence the sales user performs at the counter; before the
    fix the second step failed for every customer produced by the first.
    """
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}

    cid = await _make_customer(client, auth, "Counter Customer", "+998901110002")
    r = await _issue_contract(client, auth, cid, months=3, price="3000000")
    assert r.status_code == 201, f"{r.status_code} {r.text[:300]}"

    # And the contract is real — it appears in the tenant's own list.
    rl = await client.get("/api/tenant/installments", headers=auth)
    assert rl.status_code == 200
    assert any(i["id"] == r.json()["id"] for i in rl.json()["items"])


async def _my_contract_ids(client, auth) -> set[str]:
    r = await client.get("/api/me/installments", headers=auth)
    assert r.status_code == 200, f"me/installments: {r.status_code} {r.text[:300]}"
    body = r.json()
    rows = body["items"] if isinstance(body, dict) else body
    return {i["id"] for i in rows}


async def test_customer_links_telegram_after_the_contract_exists(isolation_ctx) -> None:
    """A customer may join later and then see the contracts already issued to them."""
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}

    cid = await _make_customer(client, auth, "Later Joiner", "+998901110003")
    r = await _issue_contract(client, auth, cid)
    assert r.status_code == 201, f"{r.status_code} {r.text[:300]}"
    contract_id = r.json()["id"]

    # Before joining, that Telegram account sees nothing — the contract exists
    # but is not reachable from any customer surface.
    assert contract_id not in await _my_contract_ids(client, ctx["auth_a"])

    # The customer joins through the real invite + signed-initData flow.
    await _join_via_invite(
        client, auth, cid, ctx["tg_a"], name="Later Joiner", phone="+998901110003",
    )

    # Now the same contract is visible on the customer's own surface.
    assert contract_id in await _my_contract_ids(client, ctx["auth_a"]), \
        "a customer who links Telegram later must see contracts already issued to them"


async def test_linking_grants_only_that_customers_contracts(isolation_ctx) -> None:
    """Identity binding is unchanged: one Telegram account, one customer record."""
    client, ctx = isolation_ctx
    auth_a = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    auth_b = {**ctx["auth_b"], "X-Tenant-Id": ctx["tenant_b"]}

    # Tenant A sells to a walk-in who then joins as Telegram user A.
    cid_a = await _make_customer(client, auth_a, "A Customer", "+998901110004")
    ra = await _issue_contract(client, auth_a, cid_a)
    assert ra.status_code == 201
    await _join_via_invite(client, auth_a, cid_a, ctx["tg_a"], name="A Customer", phone="+998901110004")

    # Tenant B sells to a different walk-in, who joins as Telegram user B.
    cid_b = await _make_customer(client, auth_b, "B Customer", "+998901110005")
    rb = await _issue_contract(client, auth_b, cid_b)
    assert rb.status_code == 201
    await _join_via_invite(client, auth_b, cid_b, ctx["tg_b"], name="B Customer", phone="+998901110005")

    # Each customer sees exactly their own contract — never the other's.
    seen_a = await _my_contract_ids(client, ctx["auth_a"])
    seen_b = await _my_contract_ids(client, ctx["auth_b"])
    assert ra.json()["id"] in seen_a
    assert rb.json()["id"] not in seen_a, "cross-customer contract leaked into A's view"
    assert rb.json()["id"] in seen_b
    assert ra.json()["id"] not in seen_b, "cross-customer contract leaked into B's view"

    # And the detail endpoint refuses the other customer's contract outright.
    assert (await client.get(f"/api/me/installments/{rb.json()['id']}", headers=ctx["auth_a"])).status_code == 404


async def test_no_cross_tenant_contract_creation(isolation_ctx) -> None:
    """Removing the join gate must not weaken tenant ownership checks."""
    client, ctx = isolation_ctx
    auth_a = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}

    # Tenant B's seeded customer also has no telegram_id. Tenant A must still be
    # refused — the guard that matters is ownership, not Telegram.
    r = await _issue_contract(client, auth_a, ctx["customer_b"])
    assert r.status_code == 404, f"expected customer_not_found, got {r.status_code} {r.text[:300]}"
    assert "customer_not_found" in r.text


def _create_installment_source() -> str:
    """The body of `create_installment`, for structural assertions below."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1]
           / "app" / "api" / "tenant" / "installments.py").read_text(encoding="utf-8")
    start = src.index("async def create_installment(")
    return src[start:src.index("\n@router.", start)]


def test_telegram_gate_is_gone_from_the_contract_path() -> None:
    """No code path may reintroduce the 409 that blocked the walk-in sale."""
    assert "customer_not_joined" not in _create_installment_source(), \
        "the Telegram pre-join gate reappeared in the contract-creation path"


def test_real_eligibility_guards_are_untouched() -> None:
    """UX-001 removed exactly ONE condition — the other guards must remain.

    A structural assertion, deliberately: customer `pending`/`blocked` states are
    reached only through the audited self-onboarding and reject flows, which have
    their own coverage. What this pins is narrower and is the actual regression
    risk of a UX change — that the edit removed the Telegram check and nothing
    else from the same block.
    """
    body = _create_installment_source()
    assert "customer_not_found" in body, "tenant-ownership check went missing"
    assert "customer_pending_approval" in body, "pending-customer guard went missing"
    assert "customer_blocked" in body, "blocked-customer guard went missing"
    assert "Customer.tenant_id == tenant.id" in body, "tenant scoping went missing"
    assert "idempotency_key" in body, "contract idempotency went missing"
    assert "pg_advisory_xact_lock" in body, "plan-limit serialisation went missing"
