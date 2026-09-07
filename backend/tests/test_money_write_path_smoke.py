"""Money write-path E2E smoke: an authenticated owner can issue a contract and
record a payment against it through the FULL HTTP + middleware stack.

Complements test_write_path_smoke (customer CRUD): there was no HTTP test exercising
the *money* mutations — `POST /installments` and `POST /payments` — at all, so a
regression in the contract/payment write path (or a middleware that blocked it, like
#843) would only surface in production. This walks the real flow and asserts the
recorded payment actually applies to the installment ledger.
"""
from __future__ import annotations


async def test_owner_issues_contract_and_records_payment(isolation_ctx, monkeypatch) -> None:
    from app.services import customer_outreach

    notified: list[str] = []

    async def _record_notification(_db, *, installment, **_kwargs) -> bool:
        notified.append(str(installment.id))
        return True

    # This is a money-path smoke test, not a Telegram integration test. Keep the
    # external boundary deterministic while proving the linked-customer hook ran.
    monkeypatch.setattr(customer_outreach, "notify_new_contract", _record_notification)

    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}

    # 1. An active customer who has joined via Telegram. Since Phase 3 UX-001 a
    #    Telegram link is NOT required to issue a contract (see
    #    test_p3_ux001_walkin_contract.py for the walk-in path) — it is set here
    #    only so this smoke test also covers the linked-customer variant.
    rc = await client.post(
        "/api/tenant/customers", headers=auth,
        json={"full_name": "Payer P", "phone": "+998901119988", "telegram_id": 778899},
    )
    assert rc.status_code == 201, f"create customer: {rc.status_code} {rc.text[:300]}"
    cid = rc.json()["id"]

    # 2. Issue a 6-month installment (a money write-path).
    ri = await client.post(
        "/api/tenant/installments", headers=auth,
        json={
            "customer_id": cid, "months": 6,
            "items": [{"product_name_snapshot": "Phone", "unit_price": "6000000", "qty": 1}],
        },
    )
    assert ri.status_code == 201, f"create installment: {ri.status_code} {ri.text[:300]}"
    iid = ri.json()["id"]
    assert ri.json()["total_amount"] == 6000000
    assert notified == [iid]

    # 3. The owner records-and-approves a cash payment — the core money mutation.
    rp = await client.post(
        "/api/tenant/payments", headers=auth,
        json={"installment_id": iid, "amount": 1000000, "method": "cash", "mark_approved": True},
    )
    assert rp.status_code == 201, f"record payment: {rp.status_code} {rp.text[:300]}"
    assert rp.json()["status"] == "approved"

    # 4. The payment actually applied to the ledger — paid amount advanced.
    rg = await client.get(f"/api/tenant/installments/{iid}", headers=auth)
    assert rg.status_code == 200
    assert rg.json()["paid_amount"] == 1000000
