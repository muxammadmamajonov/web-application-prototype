"""(#248) Customer self-service: data export (across shops) + erasure-request (not erase)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import me
from app.models import Customer, Notification, Tenant, TenantUser, User
from app.models.enums import TenantRole


async def test_export_and_erasure_request(migrated_db, grant_verified_phone) -> None:
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        # one person, a customer at TWO shops (same telegram_id)
        person = User(telegram_id=2480001, first_name="Person", phone="+998900002480")
        grant_verified_phone(person, "+998900002480")
        s.add(person)
        await s.flush()
        t1, t2 = Tenant(slug="pshop1", name="P Shop 1"), Tenant(slug="pshop2", name="P Shop 2")
        s.add_all([t1, t2])
        await s.flush()
        owner1 = User(telegram_id=2480002, first_name="Owner1")
        s.add(owner1)
        await s.flush()
        s.add(TenantUser(tenant_id=t1.id, user_id=owner1.id, role=TenantRole.owner))
        s.add_all([
            Customer(tenant_id=t1.id, full_name="Person", phone="+998900002480",
                     telegram_id=2480001, passport_id="AB1234567"),
            Customer(tenant_id=t2.id, full_name="Person", phone="+998900002480", telegram_id=2480001),
        ])
        await s.commit()

        # export spans both shops, includes the subject
        exp = await me.my_data_export(user=person, db=s)
        assert len(exp["shops"]) == 2
        assert exp["subject"]["telegram_id"] == 2480001

        # erasure-request: logs + notifies owner1, but does NOT erase
        res = await me.my_erasure_request(user=person, db=s)
        assert res["requested"] == 2
        still_there = (await s.execute(
            select(Customer).where(Customer.telegram_id == 2480001)
        )).scalars().all()
        assert len(still_there) == 2                                   # not erased — a request only
        notes = (await s.execute(
            select(Notification).where(
                Notification.user_id == owner1.id, Notification.kind == "erasure_request",
            )
        )).scalars().all()
        assert len(notes) == 1                                         # shop owner notified
