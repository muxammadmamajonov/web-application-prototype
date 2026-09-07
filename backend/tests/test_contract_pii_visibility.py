"""The contract PDF obeys the same PII rule as the customer page (MUDD-139).

MUDD-55 settled who may read a customer's identity documents inside a shop:
``owner``/``manager`` see ``passport_*``/``jshshir``/``birthdate``/``address``, a
``sales`` member sees ``***`` (``_customer_dict``) and is refused the raw ID scan
(``/customers/{cid}/kyc-media`` → 403).

The contract PDF was outside that rule. ``_can_access_installment`` admits any
active member of the owning tenant with no role check, and the contract template
prints those exact fields verbatim — so a salesperson shown ``***`` on the
customer page read the real values by opening that same customer's contract, one
contract at a time, for every customer in the shop. ``POST /downloads/grant``
widened it to a 10-minute *unauthenticated* URL for the same document.

These tests pin the closure from both directions: masked for ``sales``, and still
UNMASKED for the roles and for the customer whose own passport it is — a fix that
masked everybody would be a different bug (an unusable legal document).
"""
from __future__ import annotations

import io

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests import _dbcreds

pytestmark = pytest.mark.asyncio(loop_scope="module")

#: Values seeded onto customer A below. Each is checked against the PDF's
#: extracted text, so a mask that only hides one field cannot pass.
PASSPORT_SERIES = "AA"
PASSPORT_NUMBER = "7654321"
PASSPORT_ID = "AA7654321"
JSHSHIR = "30112900010001"
ADDRESS = "Chilonzor 7-uy"
SECRETS = (PASSPORT_ID, PASSPORT_NUMBER, JSHSHIR, ADDRESS)


@pytest_asyncio.fixture(loop_scope="module")
async def pii_world(rls_world, monkeypatch):
    """``rls_world`` plus a ``sales`` member of shop A and a customer with PII."""
    from sqlalchemy import select

    import app.db as db
    from app.config import settings
    from app.db import get_db
    from app.main import app
    from app.models import Customer, TenantUser, User
    from app.models.enums import TenantRole

    Seed = async_sessionmaker(rls_world.engine, expire_on_commit=False)
    ids: dict[str, str] = {}
    async with Seed() as s:
        # Idempotent: this fixture is function-scoped (it needs `monkeypatch`),
        # so every test in the module re-enters it against the SAME module-scoped
        # `rls_world` database. Re-inserting would collide on `ix_users_telegram_id`.
        sales = (await s.execute(
            select(User).where(User.telegram_id == 970000077)
        )).scalar_one_or_none()
        if sales is None:
            sales = User(telegram_id=970000077, first_name="Sales A")
            s.add(sales)
            await s.flush()
            s.add(TenantUser(tenant_id=rls_world.tenant_a, user_id=sales.id,
                             role=TenantRole.sales, is_active=True))
        ids["sales"] = str(sales.id)

        c = await s.get(Customer, rls_world.customer_a)
        c.passport_id = PASSPORT_ID
        c.passport_series = PASSPORT_SERIES
        c.passport_number = PASSPORT_NUMBER
        c.jshshir = JSHSHIR
        c.address = ADDRESS
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


def _pdf_text(payload: bytes) -> str:
    """The PDF's rendered text.

    Read with pypdf rather than scanned as bytes on purpose: a PDF stores text
    in compressed object streams, so ``b"7654321" in payload`` is False for a
    document that displays the number in 14pt on page one. A regex over the raw
    bytes is the reason a leak like this can survive a "we grep for it" check.
    """
    from pypdf import PdfReader
    return "\n".join(page.extract_text() or ""
                     for page in PdfReader(io.BytesIO(payload)).pages)


async def _contract_text(client, world, headers) -> str:
    import app.db as db
    db.reset_db_scope()
    r = await client.get(
        f"/api/tenant/installments/{world.installment_a}/contract.pdf",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return _pdf_text(r.content)


async def test_sales_cannot_read_customer_pii_through_the_contract(pii_world):
    """The hole itself: masked on the customer page, masked in the PDF too."""
    import app.db as db
    client, world, ids = pii_world
    headers = {**_auth(ids["sales"]), "X-Tenant-Id": world.tenant_a}

    # Precondition — MUDD-55 still masks the customer page for this role. If
    # this ever flips, the PDF assertion below would pass for the wrong reason.
    db.reset_db_scope()
    page = await client.get(f"/api/tenant/customers/{world.customer_a}",
                            headers=headers)
    assert page.status_code == 200, page.text
    assert page.json()["pii_masked"] is True
    assert page.json()["passport_id"] == "***"

    text = await _contract_text(client, world, headers)
    leaked = [s for s in SECRETS if s in text]
    assert not leaked, f"contract PDF leaked PII to a sales member: {leaked}"
    # The document is still usable — it is masked, not refused or emptied.
    assert "Alice" in text


async def test_owner_still_reads_the_real_contract(pii_world):
    """The other direction: masking everybody would be a different bug."""
    client, world, _ = pii_world
    headers = {**_auth(world.owner_a), "X-Tenant-Id": world.tenant_a}

    text = await _contract_text(client, world, headers)
    missing = [s for s in SECRETS if s not in text]
    assert not missing, f"owner's contract lost fields it must keep: {missing}"


async def test_the_customer_still_reads_their_own_passport(pii_world):
    """A person's own passport number on their own contract is not a leak."""
    import app.db as db
    client, world, _ = pii_world

    db.reset_db_scope()
    r = await client.get(
        f"/api/tenant/installments/{world.installment_a}/contract.pdf",
        headers=_auth(world.customer_user),
    )
    assert r.status_code == 200, r.text
    text = _pdf_text(r.content)
    missing = [s for s in SECRETS if s not in text]
    assert not missing, f"customer's own contract was masked: {missing}"


async def test_the_signed_download_carries_the_masking_decision(pii_world):
    """The 10-minute URL is unauthenticated, so visibility must be signed in.

    ``/api/dl/...`` has no caller identity — whatever the link says is what gets
    rendered. So the answer travels as the ``kind`` INSIDE the signed payload
    (``contract_masked``), which a holder cannot edit without the signing key.
    A query parameter, or re-deciding at fetch time, would both hand the masked
    link back its unmasked document.
    """
    import app.db as db
    client, world, ids = pii_world

    async def grant(user_id: str) -> str:
        db.reset_db_scope()
        r = await client.post(
            "/api/downloads/grant",
            headers={**_auth(user_id), "X-Tenant-Id": world.tenant_a},
            json={"kind": "contract", "id": world.installment_a},
        )
        assert r.status_code == 200, r.text
        return r.json()["url"]

    sales_url = await grant(ids["sales"])
    owner_url = await grant(world.owner_a)
    assert "/contract_masked/" in sales_url, sales_url
    assert "/contract_masked/" not in owner_url, owner_url

    # And the bytes behind each link match what the link promised.
    for url, must_be_masked in ((sales_url, True), (owner_url, False)):
        db.reset_db_scope()
        r = await client.get(url[url.index("/api/"):])
        assert r.status_code == 200, r.text
        text = _pdf_text(r.content)
        found = [s for s in SECRETS if s in text]
        if must_be_masked:
            assert not found, f"masked signed URL served real PII: {found}"
        else:
            assert len(found) == len(SECRETS), f"owner's signed URL lost PII: {found}"


async def test_a_sales_member_cannot_forge_the_unmasked_kind(pii_world):
    """Editing ``contract_masked`` back to ``contract`` in the URL must not verify."""
    import app.db as db
    client, world, ids = pii_world

    db.reset_db_scope()
    r = await client.post(
        "/api/downloads/grant",
        headers={**_auth(ids["sales"]), "X-Tenant-Id": world.tenant_a},
        json={"kind": "contract", "id": world.installment_a},
    )
    assert r.status_code == 200, r.text
    path = r.json()["url"]
    path = path[path.index("/api/"):]
    forged = path.replace("/contract_masked/", "/contract/")

    db.reset_db_scope()
    bad = await client.get(forged)
    assert bad.status_code >= 400, (
        f"a hand-edited download kind verified: {bad.status_code}"
    )


async def test_the_telegram_handoff_masks_for_sales_too(pii_world, monkeypatch):
    """The THIRD delivery path — ``deliver="telegram"`` — must mask as well.

    ``POST /downloads/grant`` has three ways out: a signed URL, this Telegram
    hand-off, and the direct ``/api/tenant/...`` route. The other two are pinned
    above. This one was not, and it is the one whose correctness rests on a
    single identifier: the hand-off renders ``wire_kind``, and ``body.kind`` — the
    unmasked value — is four lines away in the same function. Reverting that one
    word ships a ``sales`` member the fully unmasked contract into their private
    chat, and every other test in this repository stays green while it does
    (70 of them, checked).

    The bytes are captured at ``_send``, not at ``render``: ``_send`` is the last
    thing before Telegram, so this asserts on what the person actually receives
    rather than on an intermediate the delivery layer could still swap.
    """
    import app.db as db
    import app.services.document_delivery as dd
    from app.services.document_delivery import DeliveryState

    client, world, ids = pii_world
    sent: list[bytes] = []

    async def _fake_send(*, telegram_id, data, filename, caption, file_id=None):
        sent.append(data)
        return DeliveryState.sent

    monkeypatch.setattr(dd, "_send", _fake_send)

    async def deliver(user_id: str) -> str:
        sent.clear()
        db.reset_db_scope()
        r = await client.post(
            "/api/downloads/grant",
            headers={**_auth(user_id), "X-Tenant-Id": world.tenant_a},
            json={"kind": "contract", "id": world.installment_a,
                  "deliver": "telegram"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["sent"] is True, body
        # This path deliberately returns no link; if one ever appears it is a
        # live capability the masking decision would have to be re-proved on.
        assert body.get("url") is None, body
        assert len(sent) == 1, f"expected exactly one send, got {len(sent)}"
        return _pdf_text(sent[0])

    sales_text = await deliver(ids["sales"])
    leaked = [s for s in SECRETS if s in sales_text]
    assert not leaked, f"Telegram hand-off delivered PII to a sales member: {leaked}"
    # Masked, not refused or emptied — same bar as the signed URL above.
    assert "Alice" in sales_text

    # Control: the same path, an owner. A fix that masked every Telegram copy
    # would pass the assertion above and still be wrong.
    owner_text = await deliver(world.owner_a)
    missing = [s for s in SECRETS if s not in owner_text]
    assert not missing, f"owner's Telegram copy lost fields it must keep: {missing}"
