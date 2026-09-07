"""#95 (RLS) — comprehensive row-level security integration tests.

Tests that verify tenant isolation works at the database level by exercising
the actual RLS policies. These tests run against a live Postgres connection with
the full migration stack applied (including the muddato_app role + policies from
migration r73).

Two tenants (A, B) are created with data (customers, installments, payments,
notifications) in each. The tests then switch roles and verify that:

1. A query run as muddato_app with tenant_a context can't read B's rows.
2. A query run as muddato_app with tenant_b context can't read A's rows.
3. Platform queries (as postgres, which bypasses RLS) can read both.

This proves the second isolation layer (database + RLS policies) works
independently of the app's WHERE tenant_id clauses. Tests are marked
@pytest.mark.integration so they can be skipped locally if the test DB
is not available.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker


@pytest.mark.integration
async def test_rls_customer_read_isolation(migrated_db) -> None:
    """Tenant A (as muddato_app role) cannot read tenant B's customers."""
    from app.db import Base, _rls_set_local_sql
    from app.models import Customer, Tenant

    # Ensure schema exists.
    async with migrated_db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Clean up.
    async with migrated_db.begin() as conn:
        await conn.execute(text("DELETE FROM customers CASCADE"))
        await conn.execute(text("DELETE FROM tenants CASCADE"))

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    # Seed two tenants with a customer each.
    async with Session() as s:
        ta = Tenant(slug="tenant-a-rls", name="Tenant A RLS")
        tb = Tenant(slug="tenant-b-rls", name="Tenant B RLS")
        s.add_all([ta, tb])
        await s.flush()

        ca = Customer(tenant_id=ta.id, full_name="Alice A", phone="+998900000001")
        cb = Customer(tenant_id=tb.id, full_name="Bob B", phone="+998900000002")
        s.add_all([ca, cb])
        await s.commit()

    # Query as muddato_app with tenant_a context — should NOT see cb.
    async with migrated_db.begin() as conn:
        # Apply RLS scope for tenant A.
        for sql, params in _rls_set_local_sql(str(ta.id)):
            await conn.execute(text(sql), params)

        # Query should return only ca, not cb.
        result = await conn.execute(text("SELECT id, full_name FROM customers ORDER BY full_name"))
        rows = result.fetchall()
        names = {r[1] for r in rows}
        assert "Alice A" in names, "Tenant A should see their own customer"
        assert "Bob B" not in names, "Tenant A should NOT see tenant B's customer (RLS violation!)"


@pytest.mark.integration
async def test_rls_payment_read_isolation(migrated_db) -> None:
    """Tenant B (as muddato_app role) cannot read tenant A's payments."""
    from app.db import Base, _rls_set_local_sql
    from app.models import Customer, Installment, Payment, Tenant

    # Ensure schema exists.
    async with migrated_db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Clean and seed.
    async with migrated_db.begin() as conn:
        await conn.execute(text("DELETE FROM payments CASCADE"))
        await conn.execute(text("DELETE FROM installments CASCADE"))
        await conn.execute(text("DELETE FROM customers CASCADE"))
        await conn.execute(text("DELETE FROM tenants CASCADE"))

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    async with Session() as s:
        ta = Tenant(slug="tenant-a-payment", name="Tenant A Payment")
        tb = Tenant(slug="tenant-b-payment", name="Tenant B Payment")
        s.add_all([ta, tb])
        await s.flush()

        ca = Customer(tenant_id=ta.id, full_name="Alice", phone="+998900000001")
        cb = Customer(tenant_id=tb.id, full_name="Bob", phone="+998900000002")
        s.add_all([ca, cb])
        await s.flush()

        # A payment needs a parent installment (installment_id is NOT NULL).
        ia = Installment(currency="UZS",
            tenant_id=ta.id, customer_id=ca.id,
            total_amount=Decimal("100.00"), principal=Decimal("100.00"),
            months=1, monthly_amount=Decimal("100.00"), start_date=date.today(),
        )
        ib = Installment(currency="UZS",
            tenant_id=tb.id, customer_id=cb.id,
            total_amount=Decimal("200.00"), principal=Decimal("200.00"),
            months=1, monthly_amount=Decimal("200.00"), start_date=date.today(),
        )
        s.add_all([ia, ib])
        await s.flush()

        # Add a payment to each customer.
        pa = Payment(currency="UZS",
            tenant_id=ta.id,
            installment_id=ia.id,
            customer_id=ca.id,
            amount=Decimal("100.00"),
            method="cash",
        )
        pb = Payment(currency="UZS",
            tenant_id=tb.id,
            installment_id=ib.id,
            customer_id=cb.id,
            amount=Decimal("200.00"),
            method="card",
        )
        s.add_all([pa, pb])
        await s.commit()

    # Query as muddato_app with tenant_b context — should NOT see pa.
    async with migrated_db.begin() as conn:
        for sql, params in _rls_set_local_sql(str(tb.id)):
            await conn.execute(text(sql), params)

        result = await conn.execute(text("SELECT id, amount FROM payments ORDER BY amount"))
        rows = result.fetchall()
        amounts = {float(r[1]) for r in rows}
        assert 200.0 in amounts, "Tenant B should see their own payment"
        assert 100.0 not in amounts, "Tenant B should NOT see tenant A's payment (RLS violation!)"


@pytest.mark.integration
async def test_rls_installment_read_isolation(migrated_db) -> None:
    """Cross-tenant installment read is blocked by RLS."""
    from app.db import Base, _rls_set_local_sql
    from app.models import Customer, Installment, Tenant

    async with migrated_db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with migrated_db.begin() as conn:
        await conn.execute(text("DELETE FROM installments CASCADE"))
        await conn.execute(text("DELETE FROM customers CASCADE"))
        await conn.execute(text("DELETE FROM tenants CASCADE"))

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    async with Session() as s:
        ta = Tenant(slug="tenant-a-inst", name="Tenant A Inst")
        tb = Tenant(slug="tenant-b-inst", name="Tenant B Inst")
        s.add_all([ta, tb])
        await s.flush()

        ca = Customer(tenant_id=ta.id, full_name="Alice", phone="+998900000001")
        cb = Customer(tenant_id=tb.id, full_name="Bob", phone="+998900000002")
        s.add_all([ca, cb])
        await s.flush()

        ia = Installment(currency="UZS",
            tenant_id=ta.id,
            customer_id=ca.id,
            total_amount=Decimal("50.00"), principal=Decimal("50.00"),
            months=1, monthly_amount=Decimal("50.00"), start_date=date.today(),
        )
        ib = Installment(currency="UZS",
            tenant_id=tb.id,
            customer_id=cb.id,
            total_amount=Decimal("75.00"), principal=Decimal("75.00"),
            months=1, monthly_amount=Decimal("75.00"), start_date=date.today(),
        )
        s.add_all([ia, ib])
        await s.commit()

    # Query as muddato_app with tenant_a context.
    async with migrated_db.begin() as conn:
        for sql, params in _rls_set_local_sql(str(ta.id)):
            await conn.execute(text(sql), params)

        result = await conn.execute(text("SELECT id, total_amount FROM installments ORDER BY total_amount"))
        rows = result.fetchall()
        amounts = {float(r[1]) for r in rows}
        assert 50.0 in amounts, "Tenant A should see their installment"
        assert 75.0 not in amounts, "Tenant A should NOT see tenant B's installment (RLS violation!)"


@pytest.mark.integration
async def test_rls_notification_read_isolation(migrated_db) -> None:
    """Cross-tenant notification read is blocked by RLS."""
    from app.db import Base, _rls_set_local_sql
    from app.models import Notification, Tenant, User

    async with migrated_db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with migrated_db.begin() as conn:
        await conn.execute(text("DELETE FROM notifications CASCADE"))
        await conn.execute(text("DELETE FROM tenants CASCADE"))
        await conn.execute(text("DELETE FROM users CASCADE"))

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    async with Session() as s:
        ta = Tenant(slug="tenant-a-notif", name="Tenant A Notif")
        tb = Tenant(slug="tenant-b-notif", name="Tenant B Notif")
        s.add_all([ta, tb])
        await s.flush()

        # A notification belongs to a user (user_id is NOT NULL); users are global
        # (no tenant_id), so one recipient is enough — RLS isolates on tenant_id.
        u = User(telegram_id=920000001)
        s.add(u)
        await s.flush()

        na = Notification(tenant_id=ta.id, user_id=u.id, kind="reminder", title="Notification A")
        nb = Notification(tenant_id=tb.id, user_id=u.id, kind="reminder", title="Notification B")
        s.add_all([na, nb])
        await s.commit()

    # Query as muddato_app with tenant_b context.
    async with migrated_db.begin() as conn:
        for sql, params in _rls_set_local_sql(str(tb.id)):
            await conn.execute(text(sql), params)

        result = await conn.execute(text("SELECT id, title FROM notifications ORDER BY title"))
        rows = result.fetchall()
        titles = {r[1] for r in rows}
        assert "Notification B" in titles, "Tenant B should see their notification"
        assert "Notification A" not in titles, "Tenant B should NOT see tenant A's notification (RLS violation!)"


@pytest.mark.integration
async def test_rls_write_isolation_insert_blocked(migrated_db) -> None:
    """Tenant A (as muddato_app) cannot INSERT a row with tenant_b's tenant_id."""
    from app.db import Base, _rls_set_local_sql
    from app.models import Tenant

    async with migrated_db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with migrated_db.begin() as conn:
        await conn.execute(text("DELETE FROM customers CASCADE"))
        await conn.execute(text("DELETE FROM tenants CASCADE"))

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    async with Session() as s:
        ta = Tenant(slug="tenant-a-insert", name="Tenant A Insert")
        tb = Tenant(slug="tenant-b-insert", name="Tenant B Insert")
        s.add_all([ta, tb])
        await s.commit()

    # Try to INSERT as muddato_app with tenant_a context but force tenant_b in the row.
    async with migrated_db.begin() as conn:
        for sql, params in _rls_set_local_sql(str(ta.id)):
            await conn.execute(text(sql), params)

        # Attempt to insert a customer with tenant_b's tenant_id while in tenant_a context.
        try:
            await conn.execute(
                text(
                    "INSERT INTO customers (id, tenant_id, full_name, phone) "
                    "VALUES (:id, :tid, :name, :phone)"
                ),
                {
                    "id": str(uuid4()),
                    "tid": str(tb.id),
                    "name": "Hacker",
                    "phone": "+998900000099",
                },
            )
            await conn.commit()
            # If we reach here, RLS didn't block the write — test fails.
            pytest.fail("RLS WITH CHECK should have blocked the INSERT across tenants")
        except Exception as e:
            # RLS policy should reject this as a WITH CHECK violation.
            assert "policy" in str(e).lower() or "check" in str(e).lower(), (
                f"Expected RLS policy violation, got: {e}"
            )


@pytest.mark.integration
async def test_rls_write_isolation_update_blocked(migrated_db) -> None:
    """Tenant A (as muddato_app) cannot UPDATE a row to belong to tenant_b."""
    from app.db import Base, _rls_set_local_sql
    from app.models import Customer, Tenant

    async with migrated_db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with migrated_db.begin() as conn:
        await conn.execute(text("DELETE FROM customers CASCADE"))
        await conn.execute(text("DELETE FROM tenants CASCADE"))

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    async with Session() as s:
        ta = Tenant(slug="tenant-a-update", name="Tenant A Update")
        tb = Tenant(slug="tenant-b-update", name="Tenant B Update")
        s.add_all([ta, tb])
        await s.flush()

        ca = Customer(tenant_id=ta.id, full_name="Alice", phone="+998900000001")
        s.add_all([ca])
        await s.commit()

    # Try to UPDATE as muddato_app with tenant_a context to move ca to tenant_b.
    async with migrated_db.begin() as conn:
        for sql, params in _rls_set_local_sql(str(ta.id)):
            await conn.execute(text(sql), params)

        try:
            await conn.execute(
                text("UPDATE customers SET tenant_id = :tid WHERE id = :cid"),
                {"tid": str(tb.id), "cid": str(ca.id)},
            )
            await conn.commit()
            pytest.fail("RLS WITH CHECK should have blocked the UPDATE across tenants")
        except Exception as e:
            assert "policy" in str(e).lower() or "check" in str(e).lower(), (
                f"Expected RLS policy violation, got: {e}"
            )


@pytest.mark.integration
async def test_rls_postgres_bypass_still_works(migrated_db) -> None:
    """The postgres role (which has BYPASSRLS) can read all rows regardless of RLS."""
    from app.db import Base
    from app.models import Customer, Tenant

    async with migrated_db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with migrated_db.begin() as conn:
        await conn.execute(text("DELETE FROM customers CASCADE"))
        await conn.execute(text("DELETE FROM tenants CASCADE"))

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    async with Session() as s:
        ta = Tenant(slug="tenant-a-bypass", name="Tenant A Bypass")
        tb = Tenant(slug="tenant-b-bypass", name="Tenant B Bypass")
        s.add_all([ta, tb])
        await s.flush()

        ca = Customer(tenant_id=ta.id, full_name="Alice", phone="+998900000001")
        cb = Customer(tenant_id=tb.id, full_name="Bob", phone="+998900000002")
        s.add_all([ca, cb])
        await s.commit()

    # Query as postgres (no SET ROLE, so it bypasses RLS) — should see both.
    async with migrated_db.begin() as conn:
        result = await conn.execute(text("SELECT id, full_name FROM customers ORDER BY full_name"))
        rows = result.fetchall()
        names = {r[1] for r in rows}
        assert "Alice" in names, "postgres role should see A's customer"
        assert "Bob" in names, "postgres role should see B's customer (BYPASSRLS)"
