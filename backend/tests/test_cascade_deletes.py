"""Cascade-delete integrity over a REAL Postgres (#752 fix).

Deleting an ``Installment`` must take its ``Payment`` children with it — both via
the ORM relationship (``cascade="all, delete-orphan"`` on Installment.payments)
and the DB-level FK (``payments.installment_id`` is ``ON DELETE CASCADE``) — so a
removed contract never leaves orphaned payment rows behind.

These run against a real Postgres with the REAL schema (``alembic upgrade head``),
seeding through the ORM exactly like tests/test_payment_integrity.py. They SKIP
cleanly (never error) without a reachable test Postgres — the standard
``migrated_db`` / ``TEST_DATABASE_URL`` integration gate (set in CI; locally export
``TEST_DATABASE_URL``).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Customer, Installment, Payment, Tenant
from app.models.enums import InstallmentStatus, PaymentMethod, PaymentStatus


async def _seed_installment(engine) -> tuple[str, str, str]:
    """A tenant + customer + one approved installment. Returns (tenant, customer, inst) ids."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="cas-t", name="Cascade T")
        s.add(t)
        await s.flush()
        c = Customer(tenant_id=t.id, full_name="Cascade Payer", phone="+998900000111")
        s.add(c)
        await s.flush()
        inst = Installment(currency="UZS",
            tenant_id=t.id,
            customer_id=c.id,
            status=InstallmentStatus.active,
            total_amount=Decimal("100.00"),
            down_payment=Decimal("0.00"),
            principal=Decimal("100.00"),
            months=12,
            monthly_amount=Decimal("8.33"),
            paid_amount=Decimal("0.00"),
            start_date=date(2026, 6, 11),
        )
        s.add(inst)
        await s.commit()
        return str(t.id), str(c.id), str(inst.id)


def _payment(*, tenant_id: str, customer_id: str, installment_id: str) -> Payment:
    return Payment(currency="UZS",
        tenant_id=tenant_id,
        customer_id=customer_id,
        installment_id=installment_id,
        amount=Decimal("8.33"),
        method=PaymentMethod.click,
        status=PaymentStatus.pending,
    )


async def test_installment_delete_cascades_to_payments(migrated_db) -> None:
    """Deleting an installment (ORM) cascades delete to its one payment."""
    tid, cid, inst_id = await _seed_installment(migrated_db)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    async with Session() as s:
        pay = _payment(tenant_id=tid, customer_id=cid, installment_id=inst_id)
        s.add(pay)
        await s.commit()
        pay_id = str(pay.id)

    # Payment exists.
    async with Session() as s:
        assert (await s.execute(
            select(Payment).where(Payment.id == pay_id)
        )).scalar_one_or_none() is not None

    # Delete the installment through the ORM (loads + cascades the relationship).
    async with Session() as s:
        inst = await s.get(Installment, inst_id)
        await s.delete(inst)
        await s.commit()

    # Payment is gone — no orphan.
    async with Session() as s:
        assert (await s.execute(
            select(Payment).where(Payment.id == pay_id)
        )).scalar_one_or_none() is None


async def test_multiple_payments_deleted_with_installment(migrated_db) -> None:
    """Deleting an installment cascades to ALL its payments."""
    tid, cid, inst_id = await _seed_installment(migrated_db)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    payment_ids: list[str] = []
    async with Session() as s:
        for _i in range(3):
            pay = _payment(tenant_id=tid, customer_id=cid, installment_id=inst_id)
            s.add(pay)
            await s.flush()
            payment_ids.append(str(pay.id))
        await s.commit()

    # All three exist.
    async with Session() as s:
        n = (await s.execute(
            select(func.count(Payment.id)).where(Payment.installment_id == inst_id)
        )).scalar_one()
        assert n == 3

    async with Session() as s:
        inst = await s.get(Installment, inst_id)
        await s.delete(inst)
        await s.commit()

    # None remain.
    async with Session() as s:
        for payment_id in payment_ids:
            assert (await s.execute(
                select(Payment).where(Payment.id == payment_id)
            )).scalar_one_or_none() is None


async def test_no_orphaned_payments_after_cascade(migrated_db) -> None:
    """After deleting the parent installment, zero payments reference its id."""
    tid, cid, inst_id = await _seed_installment(migrated_db)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    async with Session() as s:
        s.add(_payment(tenant_id=tid, customer_id=cid, installment_id=inst_id))
        await s.commit()

    async with Session() as s:
        inst = await s.get(Installment, inst_id)
        await s.delete(inst)
        await s.commit()

    async with Session() as s:
        orphaned = (await s.execute(
            select(Payment).where(Payment.installment_id == inst_id)
        )).scalars().all()
        assert orphaned == []


async def test_db_fk_cascade_deletes_payments_on_raw_delete(migrated_db) -> None:
    """The DB-level FK (payments.installment_id ON DELETE CASCADE) is the backstop:
    a raw SQL DELETE of the parent row — bypassing the ORM relationship entirely —
    must still remove the child payments, so a code path that deletes via SQL can't
    orphan rows."""
    tid, cid, inst_id = await _seed_installment(migrated_db)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    async with Session() as s:
        s.add(_payment(tenant_id=tid, customer_id=cid, installment_id=inst_id))
        await s.commit()

    # Raw DELETE — no ORM cascade involved; only the DB FK can clean up children.
    async with Session() as s:
        await s.execute(
            text("DELETE FROM installments WHERE id = :i"), {"i": inst_id}
        )
        await s.commit()

    async with Session() as s:
        n = (await s.execute(
            select(func.count(Payment.id)).where(Payment.installment_id == inst_id)
        )).scalar_one()
        assert n == 0, "DB FK ON DELETE CASCADE must remove child payments on a raw parent delete"
