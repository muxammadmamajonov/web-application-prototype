"""A cash payment must not demand a photo of a receipt that does not exist.

REPORTED FROM USE. Choosing "cash" on the customer payment screen sent the
person to the receipt-upload page and refused to submit without an image. Cash
is handed over at the counter: there is frequently no chek to photograph, and
the shop being asked to approve the payment is the shop that took the money. The
requirement asked the customer to produce evidence for something the approver had
already witnessed, and blocked the notification entirely when they could not.

WHAT MUST NOT CHANGE while fixing it:

* **Card and transfer still require the image.** That money moved somewhere we
  cannot see, so the photo is the reviewer's only evidence.
* **The payment is still `pending`.** Nothing is credited on the customer's word;
  the tenant still approves. Removing the attachment removes an attachment, not
  the approval step.
* **Ownership, amount and remaining-balance checks are untouched.**

The endpoint also used to record EVERY manual payment as ``cash`` whatever the
customer picked, which put card money in the cash column of the shop's own
reporting. It now records what was chosen — a prerequisite for the rule above,
since "does this need proof" is a question about the method.
"""
from __future__ import annotations

import os
from decimal import Decimal

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

UPLOAD = "/api/me/payments/upload-receipt"


def _tiny_png() -> bytes:
    """A REAL png. The upload guard parses what it is given, so a handmade
    header would be rejected as `unsupported_image_type` and this suite would be
    testing the guard instead of the cash rule."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (1, 2, 3)).save(buf, "PNG")
    return buf.getvalue()


@pytest_asyncio.fixture
async def pay_ctx(monkeypatch):
    """A verified customer with one live contract, and Telegram silenced."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    from datetime import UTC, date, datetime

    import httpx
    from httpx import ASGITransport
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    # The review notification is a Telegram round-trip; this suite is about the
    # payment record, not about delivery.
    import app.services.notifications as notifications
    from app.core.security import issue_token
    from app.db import Base, get_db
    from app.main import app
    from app.models import Customer, Installment, Tenant, User
    from app.models.enums import InstallmentStatus

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(notifications, "notify_payment_review_bg", _noop)

    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with Session() as s:
            t = Tenant(slug="cash-shop", name="Cash Shop")
            s.add(t)
            await s.flush()
            buyer = User(
                telegram_id=980001, verified_phone="+998900009001",
                phone_eligibility="ELIGIBLE", verified_phone_at=datetime.now(UTC),
                verified_phone_source="telegram_contact",
            )
            s.add(buyer)
            await s.flush()
            c = Customer(tenant_id=t.id, full_name="Payer",
                         phone="+998900009001", telegram_id=980001)
            s.add(c)
            await s.flush()
            inst = Installment(
                tenant_id=t.id, customer_id=c.id, currency="UZS",
                total_amount=Decimal("1200000"), principal=Decimal("1200000"),
                down_payment=Decimal("0"), months=12,
                monthly_amount=Decimal("100000"), paid_amount=Decimal("0"),
                start_date=date.today(), status=InstallmentStatus.active,
            )
            s.add(inst)
            await s.commit()
            ctx = {
                "auth": {"Authorization": f"Bearer {issue_token(user_id=str(buyer.id))}"},
                "installment_id": str(inst.id),
                "tenant_id": str(t.id),
            }
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t",
        ) as client:
            yield client, ctx, Session
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await engine.dispose()


async def _post(client, ctx, *, method: str | None, with_file: bool, amount="50000"):
    data = {"installment_id": ctx["installment_id"], "amount": amount}
    if method is not None:
        data["method"] = method
    files = {"file": ("chek.png", _tiny_png(), "image/png")} if with_file else None
    return await client.post(UPLOAD, headers=ctx["auth"], data=data, files=files)


# ── the fix ──────────────────────────────────────────────────────────────────

async def test_cash_is_accepted_with_no_receipt(pay_ctx) -> None:
    """The reported defect. This is the whole point of the change."""
    client, ctx, _ = pay_ctx
    r = await _post(client, ctx, method="cash", with_file=False)
    assert r.status_code == 200, r.text


async def test_the_cash_payment_is_recorded_pending_and_unevidenced(pay_ctx) -> None:
    """It must be a real, reviewable payment — just without an attachment."""
    from sqlalchemy import select

    from app.models import Payment
    from app.models.enums import PaymentMethod, PaymentStatus

    client, ctx, Session = pay_ctx
    assert (await _post(client, ctx, method="cash", with_file=False)).status_code == 200

    async with Session() as s:
        pay = (await s.execute(select(Payment))).scalars().one()
    assert pay.status is PaymentStatus.pending, "cash must still await approval"
    assert pay.method is PaymentMethod.cash
    assert pay.receipt_image_url is None
    assert pay.amount == Decimal("50000")
    assert str(pay.tenant_id) == ctx["tenant_id"]


async def test_cash_still_accepts_a_photo_when_there_is_one(pay_ctx) -> None:
    """Optional means optional, not forbidden."""
    from sqlalchemy import select

    from app.models import Payment

    client, ctx, Session = pay_ctx
    r = await _post(client, ctx, method="cash", with_file=True)
    assert r.status_code == 200, r.text
    async with Session() as s:
        pay = (await s.execute(select(Payment))).scalars().one()
    assert pay.receipt_image_url is not None


# ── what must not be weakened ────────────────────────────────────────────────

async def test_card_without_a_photo_is_refused(pay_ctx) -> None:
    """Card money moved somewhere we cannot see. The image is the only evidence
    the reviewer has, so it stays mandatory — the fix is for cash alone."""
    client, ctx, _ = pay_ctx
    r = await _post(client, ctx, method="card", with_file=False)
    assert r.status_code == 400
    assert r.json()["detail"] == "receipt_required"


async def test_card_with_a_photo_is_recorded_as_card(pay_ctx) -> None:
    """Previously every manual payment was stored as cash whatever was chosen,
    which put card money in the cash column of the shop's reporting."""
    from sqlalchemy import select

    from app.models import Payment
    from app.models.enums import PaymentMethod

    client, ctx, Session = pay_ctx
    assert (await _post(client, ctx, method="card", with_file=True)).status_code == 200
    async with Session() as s:
        pay = (await s.execute(select(Payment))).scalars().one()
    assert pay.method is PaymentMethod.card
    assert pay.receipt_image_url is not None


async def test_a_method_the_product_does_not_offer_is_refused(pay_ctx) -> None:
    """`method` is caller-supplied, so it is validated against the offered set
    rather than trusted into a database column."""
    client, ctx, _ = pay_ctx
    for bogus in ("payme", "bitcoin", "transfer", "CASH; DROP TABLE payments"):
        r = await _post(client, ctx, method=bogus, with_file=False)
        assert r.status_code == 400, f"{bogus!r} was accepted"


async def test_an_older_client_that_sends_no_method_still_works(pay_ctx) -> None:
    """The field defaults to cash, so a client shipped before this change keeps
    behaving exactly as it did."""
    from sqlalchemy import select

    from app.models import Payment
    from app.models.enums import PaymentMethod

    client, ctx, Session = pay_ctx
    assert (await _post(client, ctx, method=None, with_file=True)).status_code == 200
    async with Session() as s:
        pay = (await s.execute(select(Payment))).scalars().one()
    assert pay.method is PaymentMethod.cash


async def test_a_blank_method_means_cash_deliberately(pay_ctx) -> None:
    """Blank == unset == cash, stated rather than stumbled into.

    It is the permissive reading (cash is the rail needing no proof), and it is
    safe only because the payment still lands `pending` for the tenant to
    approve — and because a customer could pick cash in the UI regardless. The
    approval gate is the control, not this label.
    """
    from sqlalchemy import select

    from app.models import Payment
    from app.models.enums import PaymentMethod, PaymentStatus

    client, ctx, Session = pay_ctx
    r = await _post(client, ctx, method="", with_file=False)
    assert r.status_code == 200, r.text
    async with Session() as s:
        pay = (await s.execute(select(Payment))).scalars().one()
    assert pay.method is PaymentMethod.cash
    assert pay.status is PaymentStatus.pending


async def test_an_unsupported_file_type_is_still_rejected(pay_ctx) -> None:
    client, ctx, _ = pay_ctx
    r = await client.post(
        UPLOAD, headers=ctx["auth"],
        data={"installment_id": ctx["installment_id"], "amount": "1000",
              "method": "cash"},
        files={"file": ("x.exe", b"MZ\x00\x00", "application/x-msdownload")},
    )
    assert r.status_code == 415


async def test_the_money_checks_are_untouched_on_the_cash_path(pay_ctx) -> None:
    """Removing the attachment must not remove the arithmetic."""
    client, ctx, _ = pay_ctx
    zero = await _post(client, ctx, method="cash", with_file=False, amount="0")
    assert zero.status_code == 400
    too_much = await _post(client, ctx, method="cash", with_file=False,
                           amount="99999999")
    assert too_much.status_code == 409
    assert too_much.json()["detail"] == "amount_exceeds_remaining"


async def test_another_persons_contract_is_still_unreachable(pay_ctx) -> None:
    """The ownership check is the tenant boundary on this route."""
    from uuid import uuid4

    client, ctx, _ = pay_ctx
    r = await client.post(
        UPLOAD, headers=ctx["auth"],
        data={"installment_id": str(uuid4()), "amount": "1000", "method": "cash"},
    )
    assert r.status_code == 404


async def test_it_is_still_authenticated(pay_ctx) -> None:
    client, ctx, _ = pay_ctx
    r = await client.post(
        UPLOAD,
        data={"installment_id": ctx["installment_id"], "amount": "1000",
              "method": "cash"},
    )
    assert r.status_code == 401
