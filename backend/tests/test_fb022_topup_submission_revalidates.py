"""FB-022 — the top-up SUBMISSION re-checks the card, not just the list.

Filtering the list endpoint is UI-deep. `card_id` arrives from the client, so an
incompatible destination is one crafted request away — a replayed request, a
stale tab left open across a settings change, or anybody who reads the network
tab. The submission endpoint therefore re-derives eligibility from the same
matrix and refuses with a named reason.

The list side is also asserted here, because "the compatible card is offered AND
the incompatible one is refused" is a single behaviour and splitting it across
files lets half of it rot.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

TOPUP_INFO = "/api/tenant/billing/topup-info"
TOPUP = "/api/tenant/billing/topup"

#: The founder's two real destination cards, in the legacy stored shape.
UZCARD = {"id": "c-uz", "bank": "Kapital Bank", "number": "5614000000002802", "scheme": "uzcard"}
VISA = {"id": "c-visa", "bank": "Kapital Bank", "number": "4413000000003478", "scheme": "visa"}
#: Network undeclared and unresolvable — withheld from every currency.
MYSTERY = {"id": "c-x", "bank": "Kapital Bank", "number": "5614682204932899"}


@pytest_asyncio.fixture
async def ctx():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    import httpx
    from httpx import ASGITransport
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.security import issue_token
    from app.db import Base, get_db
    from app.main import app
    from app.models import PlatformSetting, Tenant, TenantUser, User
    from app.models.enums import TenantRole
    from app.services.subscription_checkout import BILLING_CARDS_KEY

    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with Session() as s:
            t = Tenant(slug="topup-shop", name="Top-up Shop", balance=200_000)
            s.add(t); await s.flush()
            owner = User(telegram_id=660001)
            s.add(owner); await s.flush()
            s.add(TenantUser(tenant_id=t.id, user_id=owner.id, role=TenantRole.owner))
            s.add(PlatformSetting(key=BILLING_CARDS_KEY,
                                  value={"items": [VISA, UZCARD, MYSTERY]}))
            data = {"auth": {"Authorization": f"Bearer {issue_token(user_id=str(owner.id))}"}}
            await s.commit()
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            yield client, data
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


#: A real (minimal) PDF: bank slips arrive as PDFs at least as often as photos,
#: and the upload path validates magic bytes rather than trusting the filename.
#: A structurally complete PDF, not a fragment.
#:
#: The stub this used to be — a header, one object and a trailer with no
#: cross-reference table — no longer passes the upload guard, which now requires
#: a PDF to actually be one. A bank slip from any real bank is a real PDF, so
#: the fixture became the unrealistic thing here.
from tests.test_kyc_media import _real_pdf as _build_pdf

_PDF = _build_pdf()


def _form(card_id: str) -> dict:
    return {"data": {"amount": "50000", "method": "card", "card_id": card_id},
            "files": {"receipt": ("chek.pdf", _PDF, "application/pdf")}}


# ── the list ────────────────────────────────────────────────────────────────

async def test_the_uzs_topup_screen_never_lists_the_visa_card(ctx) -> None:
    """The exact screenshot defect: VISA offered against a 200 000 UZS balance."""
    client, d = ctx
    body = (await client.get(TOPUP_INFO, headers=d["auth"])).json()
    assert body["currency"] == "UZS"
    assert [c["id"] for c in body["cards"]] == ["c-uz"]
    assert body["cards_withheld"] is False       # a compatible card does exist


async def test_a_card_with_no_resolvable_network_is_not_listed_either(ctx) -> None:
    client, d = ctx
    listed = {c["id"] for c in (await client.get(TOPUP_INFO, headers=d["auth"])).json()["cards"]}
    assert "c-x" not in listed


# ── the submission ──────────────────────────────────────────────────────────

async def test_submitting_the_visa_card_is_refused_by_the_server(ctx) -> None:
    """Never listed — and still refused when the id is supplied by hand."""
    client, d = ctx
    r = await client.post(TOPUP, headers=d["auth"], **_form("c-visa"))
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "topup_card_currency_mismatch"


async def test_submitting_an_unresolvable_card_is_refused(ctx) -> None:
    client, d = ctx
    r = await client.post(TOPUP, headers=d["auth"], **_form("c-x"))
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "topup_card_unavailable"


async def test_submitting_an_unknown_card_id_is_refused(ctx) -> None:
    client, d = ctx
    r = await client.post(TOPUP, headers=d["auth"], **_form("nope"))
    assert r.status_code == 400
    assert r.json()["detail"] == "card_not_found"


async def test_the_compatible_card_goes_through_and_records_the_rail(ctx) -> None:
    client, d = ctx
    r = await client.post(TOPUP, headers=d["auth"], **_form("c-uz"))
    assert r.status_code == 201, r.text

    rows = (await client.get("/api/tenant/billing/topups", headers=d["auth"])).json()
    assert len(rows) == 1
    label = rows[0]["dest_card_label"]
    # Which rail the money took, months later, without the PAN.
    assert "UZCARD" in label and "UZS" in label and "···2802" in label
    assert "5614000000002802" not in label


async def test_no_compatible_card_is_a_stated_state_not_an_empty_list(ctx) -> None:
    """"none configured" and "none can take this currency" are different." """
    client, d = ctx
    from sqlalchemy import select

    from app.db import get_db
    from app.main import app
    from app.models import PlatformSetting
    from app.services.subscription_checkout import BILLING_CARDS_KEY

    gen = app.dependency_overrides[get_db]()
    s = await gen.__anext__()
    row = (await s.execute(select(PlatformSetting).where(PlatformSetting.key == BILLING_CARDS_KEY))).scalar_one()
    row.value = {"items": [VISA]}          # USD only, against a so'm wallet
    await s.commit()
    await gen.aclose()

    body = (await client.get(TOPUP_INFO, headers=d["auth"])).json()
    assert body["cards"] == []
    assert body["cards_withheld"] is True
