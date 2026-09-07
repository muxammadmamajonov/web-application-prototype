"""Who may open a receipt review, and who gets a 404 that tells them nothing.

Exercised through the real ASGI app so the dependency chain — session, tenant
resolution, role guard — is the one production uses. A unit test of the handler
body would skip exactly the layer these findings live in.

THE INDISTINGUISHABILITY RULE. "Not yours" and "does not exist" return the same
answer. A reviewer probing ids must not be able to tell a real payment in
another shop from a UUID they invented, because the difference is itself the
disclosure: it confirms that a given shop has a payment with that id.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio

from app.core.review_intent import PURPOSE_PAYMENT_REVIEW, mint
from app.models import (
    Customer,
    Installment,
    Payment,
    Tenant,
    TenantUser,
    User,
)
from app.models.enums import PaymentMethod, PaymentStatus, TenantRole


@pytest_asyncio.fixture
async def app_ctx():
    """Two shops, four people, one pending payment in shop A.

    Shop B exists so "another tenant" is a real tenant with a real owner rather
    than a hypothetical — a cross-tenant test against a nonexistent shop proves
    nothing.
    """
    import os

    import httpx
    from httpx import ASGITransport
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.security import issue_token
    from app.db import Base, get_db
    from app.main import app

    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    engine = create_async_engine(url)

    async def _reset() -> None:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))

    await _reset()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _override():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_db] = _override
    try:
        async with Session() as s:
            a = Tenant(name="Shop A", slug=f"a-{uuid.uuid4().hex[:6]}")
            b = Tenant(name="Shop B", slug=f"b-{uuid.uuid4().hex[:6]}")
            s.add_all([a, b])
            await s.flush()

            owner_a = User(telegram_id=70001)
            manager_a = User(telegram_id=70002)
            sales_a = User(telegram_id=70003)
            owner_b = User(telegram_id=70004)
            s.add_all([owner_a, manager_a, sales_a, owner_b])
            await s.flush()
            s.add_all([
                TenantUser(tenant_id=a.id, user_id=owner_a.id, role=TenantRole.owner),
                TenantUser(tenant_id=a.id, user_id=manager_a.id, role=TenantRole.manager),
                TenantUser(tenant_id=a.id, user_id=sales_a.id, role=TenantRole.sales),
                TenantUser(tenant_id=b.id, user_id=owner_b.id, role=TenantRole.owner),
            ])
            cust = Customer(tenant_id=a.id, full_name="Aliyev Vali", phone="+998901112233")
            s.add(cust)
            await s.flush()
            inst = Installment(
                tenant_id=a.id, customer_id=cust.id, currency="UZS",
                contract_number="MDD-2026-000001", start_date=date(2026, 9, 1),
                months=6, monthly_amount=Decimal("1000000"),
                total_amount=Decimal("6000000"), principal=Decimal("6000000"),
                down_payment=Decimal("0"),
            )
            s.add(inst)
            await s.flush()
            pay = Payment(
                tenant_id=a.id, installment_id=inst.id, customer_id=cust.id,
                currency="UZS", amount=Decimal("4800000"),
                method=PaymentMethod.cash, status=PaymentStatus.pending,
                paid_at=datetime(2026, 9, 1, 9, 23, tzinfo=UTC),
            )
            s.add(pay)
            await s.flush()

            ctx = {
                "tenant_a": str(a.id), "tenant_b": str(b.id),
                "payment": str(pay.id), "installment": str(inst.id),
                "owner_a": {"Authorization": f"Bearer {issue_token(user_id=str(owner_a.id))}",
                            "X-Tenant-Id": str(a.id)},
                "manager_a": {"Authorization": f"Bearer {issue_token(user_id=str(manager_a.id))}",
                              "X-Tenant-Id": str(a.id)},
                "sales_a": {"Authorization": f"Bearer {issue_token(user_id=str(sales_a.id))}",
                            "X-Tenant-Id": str(a.id)},
                "owner_b": {"Authorization": f"Bearer {issue_token(user_id=str(owner_b.id))}",
                            "X-Tenant-Id": str(b.id)},
                # Shop B's owner pointing at shop A: the header claim is not
                # membership, and this is what proves the difference.
                "owner_b_claims_a": {
                    "Authorization": f"Bearer {issue_token(user_id=str(owner_b.id))}",
                    "X-Tenant-Id": str(a.id)},
            }
            await s.commit()

        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t",
        ) as client:
            yield client, ctx
    finally:
        app.dependency_overrides.pop(get_db, None)
        # A successful approve schedules a background task, and background
        # tasks open the APPLICATION's own session factory rather than this
        # fixture's engine. That leaves a pooled connection bound to this
        # test's event loop, which a later test then finds closed. Disposing
        # returns the shared pool to a clean state.
        from app.db import engine as app_engine

        await app_engine.dispose()
        await _reset()
        await engine.dispose()


def _review_url(payment_id: str) -> str:
    return f"/api/tenant/payments/{payment_id}/review"


class TestReviewScreenAuthorization:
    async def test_an_owner_sees_everything_the_decision_needs(self, app_ctx):
        client, ctx = app_ctx
        r = await client.get(_review_url(ctx["payment"]), headers=ctx["owner_a"])
        assert r.status_code == 200, r.text
        body = r.json()
        # The mandated contents of the review screen.
        for field in ("customer_name", "contract_number", "amount", "currency",
                      "method_label", "status_label", "submitted_at", "actionable"):
            assert field in body, field
        assert body["contract_number"] == "MDD-2026-000001"
        assert body["currency"] == "UZS"
        assert body["method_label"] == "Naqd"
        assert body["status_label"] == "Kutilmoqda"
        assert body["actionable"] is True

    async def test_a_manager_may_review(self, app_ctx):
        client, ctx = app_ctx
        r = await client.get(_review_url(ctx["payment"]), headers=ctx["manager_a"])
        assert r.status_code == 200

    async def test_sales_may_not_review(self, app_ctx):
        # Sales staff take payments; they do not adjudicate them. The guard is
        # on the READ as well as the decision — evidence a person cannot act on
        # should not be readable by them either.
        client, ctx = app_ctx
        r = await client.get(_review_url(ctx["payment"]), headers=ctx["sales_a"])
        assert r.status_code == 403, r.text

    async def test_another_shop_cannot_read_it_even_claiming_this_tenant(self, app_ctx):
        client, ctx = app_ctx
        r = await client.get(_review_url(ctx["payment"]), headers=ctx["owner_b_claims_a"])
        assert r.status_code in (403, 404), r.text

    async def test_a_guessed_uuid_is_indistinguishable_from_another_shops_payment(
        self, app_ctx,
    ):
        client, ctx = app_ctx
        invented = await client.get(_review_url(str(uuid.uuid4())), headers=ctx["owner_b"])
        real_elsewhere = await client.get(_review_url(ctx["payment"]), headers=ctx["owner_b"])
        # Same status AND same body: a difference in either is the disclosure.
        assert invented.status_code == real_elsewhere.status_code
        assert invented.json() == real_elsewhere.json()

    async def test_anonymous_is_refused(self, app_ctx):
        client, ctx = app_ctx
        r = await client.get(_review_url(ctx["payment"]))
        assert r.status_code in (401, 403)

    async def test_opening_the_review_screen_does_not_approve_anything(self, app_ctx):
        # The rule the whole FB-025 design turns on.
        client, ctx = app_ctx
        await client.get(_review_url(ctx["payment"]), headers=ctx["owner_a"])
        again = await client.get(_review_url(ctx["payment"]), headers=ctx["owner_a"])
        assert again.json()["status"] == "pending"
        assert again.json()["actionable"] is True


class TestReviewIntentEndpoint:
    async def test_the_intended_reviewer_gets_a_route(self, app_ctx):
        client, ctx = app_ctx
        token = mint(PURPOSE_PAYMENT_REVIEW, uuid.UUID(ctx["payment"]))
        r = await client.post("/api/review-intents/resolve",
                              json={"intent": token}, headers=ctx["owner_a"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ok"
        assert body["route"] == f"/tenant/payments/{ctx['payment']}/review"

    async def test_forwarding_the_message_grants_nothing(self, app_ctx):
        """THE property the deep link depends on.

        A `startapp` parameter rides in a group message. Anyone can read it,
        forward it, paste it. It resolves for the person entitled to the screen
        and for nobody else — because resolution names a destination and the
        authorization is evaluated separately, against the session.
        """
        client, ctx = app_ctx
        token = mint(PURPOSE_PAYMENT_REVIEW, uuid.UUID(ctx["payment"]))
        r = await client.post("/api/review-intents/resolve",
                              json={"intent": token}, headers=ctx["owner_b"])
        assert r.status_code == 200
        assert r.json()["status"] == "unknown"
        assert r.json()["route"] is None

    async def test_sales_cannot_resolve_it_either(self, app_ctx):
        client, ctx = app_ctx
        token = mint(PURPOSE_PAYMENT_REVIEW, uuid.UUID(ctx["payment"]))
        r = await client.post("/api/review-intents/resolve",
                              json={"intent": token}, headers=ctx["sales_a"])
        assert r.json()["status"] == "unknown"

    async def test_an_anonymous_caller_never_reaches_resolution(self, app_ctx):
        client, ctx = app_ctx
        token = mint(PURPOSE_PAYMENT_REVIEW, uuid.UUID(ctx["payment"]))
        r = await client.post("/api/review-intents/resolve", json={"intent": token})
        assert r.status_code in (401, 403)

    async def test_an_unknown_intent_returns_the_same_safe_state(self, app_ctx):
        client, ctx = app_ctx
        denied = await client.post(
            "/api/review-intents/resolve",
            json={"intent": mint(PURPOSE_PAYMENT_REVIEW, uuid.uuid4())},
            headers=ctx["owner_a"])
        garbage = await client.post(
            "/api/review-intents/resolve", json={"intent": "not-a-real-intent"},
            headers=ctx["owner_a"])
        # A missing payment and a forged token are the same answer — otherwise
        # this endpoint becomes an oracle for which payment ids exist.
        assert denied.json() == garbage.json() == {
            "status": "unknown", "route": None, "tenant_id": None,
        }

    async def test_an_oversized_intent_is_rejected_by_the_schema(self, app_ctx):
        client, ctx = app_ctx
        r = await client.post("/api/review-intents/resolve",
                              json={"intent": "A" * 500}, headers=ctx["owner_a"])
        assert r.status_code == 422


class TestDownloadGrantAuthorization:
    async def test_another_shop_cannot_grant_a_contract(self, app_ctx):
        client, ctx = app_ctx
        r = await client.post("/api/downloads/grant",
                              json={"kind": "contract", "id": ctx["installment"]},
                              headers=ctx["owner_b"])
        assert r.status_code in (403, 404), r.text

    async def test_a_guessed_uuid_is_refused(self, app_ctx):
        client, ctx = app_ctx
        r = await client.post("/api/downloads/grant",
                              json={"kind": "contract", "id": str(uuid.uuid4())},
                              headers=ctx["owner_a"])
        assert r.status_code in (403, 404)

    async def test_a_non_uuid_is_refused_before_anything_is_looked_up(self, app_ctx):
        client, ctx = app_ctx
        for hostile in ("../../etc/passwd", "not-a-uuid", "1 OR 1=1"):
            r = await client.post("/api/downloads/grant",
                                  json={"kind": "contract", "id": hostile},
                                  headers=ctx["owner_a"])
            assert r.status_code in (400, 422), hostile

    async def test_a_stale_signature_does_not_serve_a_document(self, app_ctx):
        client, _ctx = app_ctx
        r = await client.get(f"/api/dl/contract/{uuid.uuid4()}?e=1&s=deadbeef")
        assert r.status_code == 403


class TestOnlyADecisionRetiresTheChatAction:
    """The Telegram edit is a consequence of a committed decision, so whoever
    cannot decide cannot cause an edit either."""

    async def _edits(self, monkeypatch):
        import app.services.notifications as notif
        import app.services.payment_review_notify as prn
        from app.models import PaymentReviewNotice

        seen: list[dict] = []

        class _Bot:
            async def edit_message_text(self, **kw):
                seen.append(kw)
                return True

        monkeypatch.setattr(notif, "get_bot", lambda: _Bot())
        return seen, prn, PaymentReviewNotice

    async def test_sales_cannot_cause_an_edit(self, app_ctx, monkeypatch):
        seen, _prn, _m = await self._edits(monkeypatch)
        client, ctx = app_ctx
        r = await client.post(
            f"/api/tenant/payments/{ctx['payment']}/approve", headers=ctx["sales_a"],
        )
        assert r.status_code == 403
        assert seen == []

    async def test_another_tenant_cannot_cause_an_edit(self, app_ctx, monkeypatch):
        seen, _prn, _m = await self._edits(monkeypatch)
        client, ctx = app_ctx
        r = await client.post(
            f"/api/tenant/payments/{ctx['payment']}/approve",
            headers=ctx["owner_b_claims_a"],
        )
        assert r.status_code in (403, 404)
        assert seen == []

    async def test_anonymous_cannot_cause_an_edit(self, app_ctx, monkeypatch):
        seen, _prn, _m = await self._edits(monkeypatch)
        client, ctx = app_ctx
        r = await client.post(f"/api/tenant/payments/{ctx['payment']}/approve")
        assert r.status_code in (401, 403)
        assert seen == []

    async def test_a_duplicate_decision_does_not_move_money_twice(self, app_ctx, monkeypatch):
        """The database transition is the guarantee. A second approval is
        refused under the row lock, so no second edit and no second credit."""
        await self._edits(monkeypatch)
        client, ctx = app_ctx
        first = await client.post(
            f"/api/tenant/payments/{ctx['payment']}/approve", headers=ctx["owner_a"],
        )
        assert first.status_code == 200, first.text
        second = await client.post(
            f"/api/tenant/payments/{ctx['payment']}/approve", headers=ctx["owner_a"],
        )
        assert second.status_code == 409
        assert second.json()["detail"] == "not_pending"

    async def test_the_review_screen_goes_read_only_after_the_decision(self, app_ctx, monkeypatch):
        await self._edits(monkeypatch)
        client, ctx = app_ctx
        await client.post(
            f"/api/tenant/payments/{ctx['payment']}/approve", headers=ctx["owner_a"],
        )
        after = await client.get(_review_url(ctx["payment"]), headers=ctx["owner_a"])
        assert after.json()["actionable"] is False
        assert after.json()["status"] == "approved"


class TestRetirementIsScheduledDurably:
    """The decision must mark the retirement work in the SAME transaction that
    commits it. A background task is process-local; a row is not."""

    async def test_approval_marks_the_notice_due_in_the_database(
        self, app_ctx, monkeypatch,
    ):
        from sqlalchemy import select

        import app.services.notifications as notif
        from app.models import PaymentReviewNotice

        class _Bot:
            async def edit_message_text(self, **kw):
                raise RuntimeError("telegram is down")

        monkeypatch.setattr(notif, "get_bot", lambda: _Bot())
        client, ctx = app_ctx

        from app.db import get_db
        from app.main import app as fastapi_app

        session_factory = fastapi_app.dependency_overrides[get_db]
        async for sess in session_factory():
            sess.add(PaymentReviewNotice(
                payment_id=uuid.UUID(ctx["payment"]),
                tenant_id=uuid.UUID(ctx["tenant_a"]),
                chat_id=99001, message_id=7, state="pending",
            ))
            await sess.commit()
            break

        r = await client.post(
            f"/api/tenant/payments/{ctx['payment']}/approve", headers=ctx["owner_a"],
        )
        assert r.status_code == 200, r.text

        async for sess in session_factory():
            row = (await sess.execute(select(PaymentReviewNotice))).scalars().one()
            break

        # Telegram was down for the whole request, so the state proves the
        # DURABLE marking rather than a successful edit.
        assert row.state in ("retry", "finalizing"), (
            "the decision committed without durably scheduling retirement"
        )
        assert row.outcome == "approved"

    async def test_rejection_marks_the_notice_due_in_the_database(
        self, app_ctx, monkeypatch,
    ):
        from sqlalchemy import select

        import app.services.notifications as notif
        from app.models import PaymentReviewNotice

        class _Bot:
            async def edit_message_text(self, **kw):
                raise RuntimeError("telegram is down")

        monkeypatch.setattr(notif, "get_bot", lambda: _Bot())
        client, ctx = app_ctx

        from app.db import get_db
        from app.main import app as fastapi_app

        session_factory = fastapi_app.dependency_overrides[get_db]
        async for sess in session_factory():
            sess.add(PaymentReviewNotice(
                payment_id=uuid.UUID(ctx["payment"]),
                tenant_id=uuid.UUID(ctx["tenant_a"]),
                chat_id=99002, message_id=8, state="pending",
            ))
            await sess.commit()
            break

        r = await client.post(
            f"/api/tenant/payments/{ctx['payment']}/reject", headers=ctx["owner_a"],
        )
        assert r.status_code == 200, r.text

        async for sess in session_factory():
            row = (await sess.execute(select(PaymentReviewNotice))).scalars().one()
            break

        assert row.state in ("retry", "finalizing")
        assert row.outcome == "rejected"

    async def test_the_decision_survives_telegram_being_down(
        self, app_ctx, monkeypatch,
    ):
        import app.services.notifications as notif

        class _Bot:
            async def edit_message_text(self, **kw):
                raise RuntimeError("telegram is down")

        monkeypatch.setattr(notif, "get_bot", lambda: _Bot())
        client, ctx = app_ctx
        r = await client.post(
            f"/api/tenant/payments/{ctx['payment']}/approve", headers=ctx["owner_a"],
        )
        assert r.status_code == 200
        after = await client.get(_review_url(ctx["payment"]), headers=ctx["owner_a"])
        assert after.json()["status"] == "approved"
