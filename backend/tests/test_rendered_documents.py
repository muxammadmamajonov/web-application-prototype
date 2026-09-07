"""What the contract and the receipt actually look like once rendered.

Every case here was reported from a document a founder-beta customer was
handed, and every one of them is a LAYOUT fact — invisible in the template
source, which is why they survived to production:

  * `[LEGAL_REVIEW]` printed in the customer's copy, in a highlighted box;
  * the footer sitting on top of the closing clauses;
  * `Sana` colliding with its own value on the 80 mm receipt;
  * `2026-09-01T14:23:11.482913+00:00`, `cash`, `approved`, `4800000.00`;
  * `≈` in front of a figure that is exact.

The suite renders through the real service against real rows, then walks the
laid-out box tree — see `_pdf_probe` — so an assertion can name the two strings
that print on top of each other.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio

from app.models import (
    Customer,
    Installment,
    InstallmentItem,
    Payment,
    Tenant,
    TenantSettings,
)
from app.models.enums import PaymentMethod, PaymentStatus
from tests._pdf_probe import all_text, collisions, find, overflows, render_pages


@pytest_asyncio.fixture
async def docs_db():
    """A session factory over a model-built schema.

    Deliberately `create_all` rather than the migration chain: these tests are
    about what a rendered document LOOKS like, so the RLS policies that
    `migrated_db` exists to exercise buy nothing here and would cost a full
    migration run per case. Tenant isolation for documents is covered by the
    authorization suite, where it belongs.
    """
    import os

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import Base

    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    from sqlalchemy import text

    engine = create_async_engine(url)
    # DROP SCHEMA rather than `drop_all`: a previous migrated run leaves RLS
    # POLICIES behind, and a policy on one table depends on another, so
    # metadata-ordered drops cannot untangle them. Same approach as conftest's
    # `migrated_db`.
    async def _reset() -> None:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))

    await _reset()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await _reset()
        await engine.dispose()

#: A name at the length a real shop hits — the case that made columns collide.
LONG_NAME = "Toshkent Shahar Chilonzor Tumani Maishiy Texnika Savdo Markazi MChJ"
LONG_CUSTOMER = "Abdurahmonov Abdulaziz Abdurahmon o'g'li Muhammadaliyevich"


async def _seed(
    sess,
    *,
    currency: str = "UZS",
    items: int = 1,
    months: int = 6,
    tenant_name: str = "Demo Electronics",
    customer_name: str = "Aliyev Vali",
    product: str = "Samsung A54",
) -> Installment:
    t = Tenant(name=tenant_name, slug=f"s-{uuid.uuid4().hex[:8]}")
    sess.add(t)
    await sess.flush()
    sess.add(TenantSettings(
        tenant_id=t.id, business_name=tenant_name,
        address="Toshkent sh., Chilonzor tumani, 12-uy",
        contact_phone="+998901112233", receipt_footer="Xaridingiz uchun rahmat!",
    ))
    c = Customer(
        tenant_id=t.id, full_name=customer_name, phone="+998901112233",
        passport_id="AA1234567", jshshir="30112900010001",
        address="Toshkent sh.",
    )
    sess.add(c)
    await sess.flush()

    monthly = Decimal("1000000")
    total = monthly * months
    inst = Installment(
        tenant_id=t.id, customer_id=c.id, currency=currency,
        contract_number="MDD-2026-000001",
        start_date=date(2026, 9, 1), months=months,
        monthly_amount=monthly, total_amount=total,
        principal=total, down_payment=Decimal("0"),
    )
    sess.add(inst)
    await sess.flush()
    for i in range(items):
        sess.add(InstallmentItem(
            installment_id=inst.id, product_name_snapshot=f"{product} #{i + 1}",
            qty=1, unit_price=total / items, line_total=total / items,
        ))
    await sess.flush()
    return inst


async def _contract_pages(db, inst_id):
    from app.services.pdf import contract_html

    return render_pages(await contract_html(db, inst_id))


async def _receipt_pages(db, payment_id):
    from app.services.pdf import receipt_html

    return render_pages(await receipt_html(db, payment_id))


# ── FB-015: the contract ────────────────────────────────────────────────────

class TestContractIsProductionClean:
    async def test_no_internal_marker_reaches_the_customer(self, docs_db):
        async with docs_db() as sess:
            inst = await _seed(sess)
            pages = await _contract_pages(sess, inst.id)
        text = all_text(pages)
        for marker in ("[LEGAL_REVIEW", "LEGAL_REVIEW", "TODO", "FIXME",
                       "PLACEHOLDER", "XXX", "tenant-default"):
            assert marker not in text, f"{marker!r} printed on the contract"

    async def test_no_raw_iso_date_in_customer_prose(self, docs_db):
        async with docs_db() as sess:
            inst = await _seed(sess)
            pages = await _contract_pages(sess, inst.id)
        text = all_text(pages)
        assert "2026-09-01" not in text
        assert "1-sentabr, 2026" in text

    async def test_money_is_grouped_not_a_raw_decimal(self, docs_db):
        async with docs_db() as sess:
            inst = await _seed(sess)
            pages = await _contract_pages(sess, inst.id)
        text = all_text(pages)
        assert "6000000.00" not in text and "6000000" not in text
        assert "6 000 000" in text.replace(" ", " ")

    async def test_the_footer_does_not_sit_on_the_clauses(self, docs_db):
        # The reported collision: a fixed footer overlapping contract text.
        async with docs_db() as sess:
            inst = await _seed(sess, months=24, items=6)
            pages = await _contract_pages(sess, inst.id)
        for n, page in enumerate(pages, 1):
            assert not collisions(page), f"page {n}: {collisions(page)[:3]}"

    async def test_nothing_is_clipped_off_any_page(self, docs_db):
        async with docs_db() as sess:
            inst = await _seed(sess, months=24, items=6)
            pages = await _contract_pages(sess, inst.id)
        for n, page in enumerate(pages, 1):
            assert not overflows(page), f"page {n}: {overflows(page)[:3]}"

    @pytest.mark.parametrize("months,items,label", [
        (3, 1, "one page"),
        (24, 8, "multi page"),
    ])
    async def test_short_and_long_contracts_both_stay_clean(
        self, docs_db, months, items, label,
    ):
        async with docs_db() as sess:
            inst = await _seed(sess, months=months, items=items)
            pages = await _contract_pages(sess, inst.id)
        assert pages
        for page in pages:
            assert not collisions(page), label
            assert not overflows(page), label

    async def test_the_longest_realistic_names_still_wrap(self, docs_db):
        async with docs_db() as sess:
            inst = await _seed(
                sess, tenant_name=LONG_NAME, customer_name=LONG_CUSTOMER,
                product="Samsung Galaxy A54 5G 256GB Awesome Graphite (rasmiy kafolat)",
                items=3,
            )
            pages = await _contract_pages(sess, inst.id)
        for page in pages:
            assert not overflows(page), f"long names clipped: {overflows(page)[:3]}"
            assert not collisions(page), f"long names collided: {collisions(page)[:3]}"

    @pytest.mark.parametrize("currency", ["UZS", "USD"])
    async def test_both_currencies_render(self, docs_db, currency):
        async with docs_db() as sess:
            inst = await _seed(sess, currency=currency)
            pages = await _contract_pages(sess, inst.id)
        text = all_text(pages)
        assert ("so'm" if currency == "UZS" else "USD") in text
        for page in pages:
            assert not collisions(page)

    async def test_an_old_contract_keeps_its_own_currency_after_the_shop_switches(
        self, docs_db,
    ):
        # The contract is the document the customer holds. Re-reading the shop's
        # CURRENT setting would reprint a signed so'm contract as USD.
        async with docs_db() as sess:
            inst = await _seed(sess, currency="UZS")
            settings_row = await sess.get(TenantSettings, inst.tenant_id)
            if settings_row is not None:
                settings_row.currency = "USD"
            await sess.flush()
            pages = await _contract_pages(sess, inst.id)
        text = all_text(pages)
        assert "so'm" in text
        assert "USD" not in text


# ── FB-019: the receipt ─────────────────────────────────────────────────────

async def _seed_payment(sess, *, currency="UZS", method=PaymentMethod.cash,
                        status=PaymentStatus.approved, amount="4800000") -> Payment:
    inst = await _seed(sess, currency=currency)
    pay = Payment(
        tenant_id=inst.tenant_id, installment_id=inst.id, customer_id=inst.customer_id,
        currency=currency, amount=Decimal(amount), method=method, status=status,
        paid_at=datetime(2026, 9, 1, 9, 23, 11, 482913, tzinfo=UTC),
    )
    sess.add(pay)
    await sess.flush()
    return pay


class TestReceiptIsProductionClean:
    async def test_the_label_no_longer_collides_with_its_value(self, docs_db):
        # "Sana" printing on top of its own timestamp — the headline defect.
        async with docs_db() as sess:
            pay = await _seed_payment(sess)
            pages = await _receipt_pages(sess, pay.id)
        for n, page in enumerate(pages, 1):
            assert not collisions(page), f"page {n}: {collisions(page)[:3]}"

    async def test_no_raw_timestamp_survives(self, docs_db):
        async with docs_db() as sess:
            pay = await _seed_payment(sess)
            pages = await _receipt_pages(sess, pay.id)
        text = all_text(pages)
        for fragment in ("482913", "+00:00", "T09:23", "2026-09-01"):
            assert fragment not in text, f"{fragment!r} on the receipt"
        # 09:23 UTC is 14:23 in the business timezone.
        assert "1-sentabr, 2026" in text and "14:23" in text

    @pytest.mark.parametrize("method,expected", [
        (PaymentMethod.cash, "Naqd"),
        (PaymentMethod.card, "Karta"),
        (PaymentMethod.transfer, "Bank o'tkazmasi"),
    ])
    async def test_the_payment_method_is_localized(self, docs_db, method, expected):
        async with docs_db() as sess:
            pay = await _seed_payment(sess, method=method)
            pages = await _receipt_pages(sess, pay.id)
        text = all_text(pages)
        assert expected in text
        assert method.value not in text

    @pytest.mark.parametrize("status,expected", [
        (PaymentStatus.approved, "Tasdiqlangan"),
        (PaymentStatus.pending, "Kutilmoqda"),
        (PaymentStatus.rejected, "Rad etilgan"),
    ])
    async def test_the_status_is_localized(self, docs_db, status, expected):
        async with docs_db() as sess:
            pay = await _seed_payment(sess, status=status)
            pages = await _receipt_pages(sess, pay.id)
        text = all_text(pages)
        assert expected in text
        assert status.value not in text

    async def test_the_amount_is_grouped(self, docs_db):
        async with docs_db() as sess:
            pay = await _seed_payment(sess, amount="4800000")
            pages = await _receipt_pages(sess, pay.id)
        text = all_text(pages).replace(" ", " ")
        assert "4800000.00" not in text
        assert "4 800 000" in text

    async def test_an_exact_balance_carries_no_approximation_sign(self, docs_db):
        # `≈` in front of a figure the ledger knows exactly is a claim that the
        # shop cannot answer "how much do I still owe" — which it can.
        async with docs_db() as sess:
            pay = await _seed_payment(sess)
            pages = await _receipt_pages(sess, pay.id)
        assert "≈" not in all_text(pages)

    async def test_the_remaining_balance_is_the_remaining_balance(self, docs_db):
        """It printed `totals.principal` — the ORIGINAL debt — under a label
        reading "Qarz qoldiq" (still owed). Not a formatting bug.

        The contract must have PAID SOMETHING, or the two figures coincide and
        the test cannot tell them apart — which is exactly how the defect
        survived its first test.
        """
        from app.core.documents import money

        async with docs_db() as sess:
            pay = await _seed_payment(sess, amount="1000000")
            inst = await sess.get(Installment, pay.installment_id)
            inst.paid_amount = Decimal("2000000")      # two instalments settled
            await sess.flush()
            expected = inst.remaining_balance
            principal = inst.principal
            pages = await _receipt_pages(sess, pay.id)

        assert expected != principal, "fixture cannot distinguish the two figures"
        text = all_text(pages)
        assert money(expected, "UZS") in text, "the remaining balance is absent"
        assert money(principal, "UZS") not in text, "the original principal was printed"

    async def test_a_long_value_cannot_print_on_top_of_its_own_label(self, docs_db):
        """The exact reported collision, at the length that produced it.

        On a 72 mm printable width a flex row gives the value whatever space is
        left over, so a long bank reference lands on `Ref`. A grid gives it a
        real column and it wraps instead.
        """
        async with docs_db() as sess:
            pay = await _seed_payment(sess)
            pay.external_ref = "TRANSFER-2026-09-01-0000123456789-BANKAPPROVALCODE-XYZ"
            await sess.flush()
            pages = await _receipt_pages(sess, pay.id)
        for n, page in enumerate(pages, 1):
            assert not collisions(page), f"page {n}: {collisions(page)[:3]}"
            assert not overflows(page), f"page {n}: {overflows(page)[:3]}"

    async def test_the_currency_comes_from_the_immutable_payment_row(self, docs_db):
        async with docs_db() as sess:
            pay = await _seed_payment(sess, currency="USD")
            pages = await _receipt_pages(sess, pay.id)
        assert "USD" in all_text(pages)

    async def test_a_long_name_does_not_run_off_an_80mm_receipt(self, docs_db):
        async with docs_db() as sess:
            inst = await _seed(sess, tenant_name=LONG_NAME, customer_name=LONG_CUSTOMER)
            pay = Payment(
                tenant_id=inst.tenant_id, installment_id=inst.id,
                customer_id=inst.customer_id, currency="UZS",
                amount=Decimal("4800000"), method=PaymentMethod.cash,
                status=PaymentStatus.approved,
                paid_at=datetime(2026, 9, 1, 9, 23, tzinfo=UTC),
            )
            sess.add(pay)
            await sess.flush()
            pages = await _receipt_pages(sess, pay.id)
        for page in pages:
            assert not overflows(page), f"clipped: {overflows(page)[:3]}"
            assert not collisions(page), f"collided: {collisions(page)[:3]}"

    async def test_one_document_one_language(self, docs_db):
        async with docs_db() as sess:
            pay = await _seed_payment(sess)
            pages = await _receipt_pages(sess, pay.id)
        text = all_text(pages)
        # No English enum or label may leak into an Uzbek document.
        for english in ("cash", "approved", "pending", "Status", "Date", "Amount"):
            assert english not in text, f"{english!r} left in an Uzbek receipt"

    async def test_no_passport_or_jshshir_on_a_payment_receipt(self, docs_db):
        # A receipt is handed across a counter and photographed. The contract
        # needs identity fields; a payment slip does not.
        async with docs_db() as sess:
            pay = await _seed_payment(sess)
            pages = await _receipt_pages(sess, pay.id)
        text = all_text(pages)
        assert "AA1234567" not in text
        assert "30112900010001" not in text


class TestDocumentsAgreeWithEachOther:
    async def test_contract_and_receipt_format_the_same_amount_identically(
        self, docs_db,
    ):
        # The reason formatting lives in one module: two documents about one
        # contract must not write the same number two ways.
        async with docs_db() as sess:
            pay = await _seed_payment(sess, amount="6000000")
            c_pages = await _contract_pages(sess, pay.installment_id)
            r_pages = await _receipt_pages(sess, pay.id)
        assert find(c_pages, "6 000 000"), "contract"
        assert find(r_pages, "6 000 000"), "receipt"
