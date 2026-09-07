"""(MUDD-138) The daily owner briefing must not invent money.

Two independent defects lived in ``daily_owner_briefing_job``:

1. **Cross-currency addition.** Both money aggregates (`monthly_amount` due
   today, and yesterday's collected `Payment.amount`) were plain SUMs with no
   ``GROUP BY currency``, and the result was printed with a hardcoded ``so'm``.
   A shop holding one UZS and one USD contract was told a total that is true of
   neither currency — and labelled it with the one it was less true of.

2. **The wrong "yesterday".** ``func.date(coalesce(paid_at, ...)) == yesterday``
   compares the DATABASE's (UTC) calendar day. Tashkent is UTC+5, so every
   payment taken between 00:00 and 05:00 local time carries the PREVIOUS UTC
   date — it was filed one day early, in both directions: yesterday's early
   payments were dropped and today's were counted as yesterday's.

The DB-backed cases below are the mutation guards: revert either fix and they
go red on a number, not on a formatting nicety.
"""
from __future__ import annotations

import inspect
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import async_sessionmaker

TASHKENT = ZoneInfo("Asia/Tashkent")


# ── the day window (no DB) ───────────────────────────────────────────────────

def test_tashkent_day_bounds_are_the_local_day_half_open() -> None:
    from app.core.business_time import tashkent_day_bounds_utc

    start, end = tashkent_day_bounds_utc(date(2026, 3, 10))
    # Tashkent is UTC+5 with no DST: the local day opens at 19:00 UTC the day
    # before and closes at 19:00 UTC on the day itself.
    assert start == datetime(2026, 3, 9, 19, 0, tzinfo=UTC)
    assert end == datetime(2026, 3, 10, 19, 0, tzinfo=UTC)
    # Half-open: the closing edge belongs to the NEXT day, so no instant is
    # counted twice and none falls in the gap.
    assert tashkent_day_bounds_utc(date(2026, 3, 11))[0] == end


def test_tashkent_day_bounds_capture_the_hours_utc_date_files_wrongly() -> None:
    """01:00 Tashkent is the previous UTC date — the whole bug in one assert."""
    from app.core.business_time import tashkent_day_bounds_utc

    day = date(2026, 3, 10)
    early = datetime.combine(day, time(1, 0), tzinfo=TASHKENT).astimezone(UTC)

    assert early.date() == date(2026, 3, 9), "premise: the UTC date disagrees"
    start, end = tashkent_day_bounds_utc(day)
    assert start <= early < end, "an 01:00 local payment belongs to its LOCAL day"


# ── the rendered figure (no DB) ──────────────────────────────────────────────

def test_briefing_money_single_currency_keeps_one_amount_and_label() -> None:
    """A shop that has never written a second currency reads what it always did."""
    from app.core.documents import money_with_currency
    from app.services.scheduler import _briefing_money

    line = _briefing_money({"UZS": Decimal("1200000")}, "UZS")
    assert line == money_with_currency(Decimal("1200000"), "UZS", "uz")
    assert line.endswith("so'm")
    assert "+" not in line, "a single-currency shop must not get a joined list"


def test_briefing_money_never_folds_two_currencies_into_one_number() -> None:
    from app.core.documents import money, money_with_currency
    from app.services.scheduler import _briefing_money

    line = _briefing_money({"UZS": Decimal("1200000"), "USD": Decimal("350")}, "UZS")

    assert money_with_currency(Decimal("1200000"), "UZS", "uz") in line
    assert money_with_currency(Decimal("350"), "USD", "uz") in line
    # The sum that the old code would have printed must appear nowhere.
    assert money(Decimal("1200350"), "UZS") not in line
    # The tenant's own currency leads; alphabetical order would put USD first.
    assert line.index("so'm") < line.index("USD")


def test_briefing_money_labels_an_empty_total_with_the_tenant_currency() -> None:
    """"Nothing collected" is denominated in the shop's currency, not a guess."""
    from app.services.scheduler import _briefing_money

    assert _briefing_money({}, "USD").endswith("USD")
    assert _briefing_money({}, "UZS").endswith("so'm")


# ── source guard ─────────────────────────────────────────────────────────────

def test_briefing_groups_money_by_currency_and_bounds_the_day() -> None:
    from app.services import scheduler

    src = inspect.getsource(scheduler.daily_owner_briefing_job)
    # Comments are stripped: the code explains the bug it fixed by NAMING the
    # old expression, and a guard that reads prose would pass on the comment.
    code = "\n".join(
        ln for ln in src.splitlines() if not ln.strip().startswith("#")
    )
    assert "group_by(Installment.currency)" in code
    assert "group_by(Payment.currency)" in code
    assert "tashkent_day_bounds_utc(" in code
    # The UTC-calendar day comparison is gone, in favour of a range test.
    assert "func.date(" not in code, "day filtering must be a [start, end) range"
    # F-19's aggregate/keyset shape is untouched.
    assert "func.count(" in code
    assert "_iter_keyset(" in code


# ── behaviour (DB-backed) ────────────────────────────────────────────────────

def _capture_telegram(monkeypatch) -> list[str]:
    """Collect every briefing body instead of calling Telegram."""
    from app.services import notifications

    sent: list[str] = []

    async def _fake(tg_id: object, text: str, *a: object, **kw: object) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(notifications, "send_to_telegram", _fake)
    return sent


async def _seed_owner(s, slug: str, tg_id: int):
    from app.models import Customer, Tenant, TenantUser, User
    from app.models.enums import TenantRole

    t = Tenant(slug=slug, name=slug)
    s.add(t)
    await s.flush()
    u = User(telegram_id=tg_id)
    s.add(u)
    await s.flush()
    c = Customer(tenant_id=t.id, full_name="Briefing Customer", phone=f"+9989{tg_id:08d}")
    s.add_all([c, TenantUser(tenant_id=t.id, user_id=u.id, role=TenantRole.owner)])
    await s.flush()
    return t, c


def _utc_sessionmaker(migrated_db):
    """Session factory pinned to ``TimeZone=UTC``, like every server we deploy to.

    This is load-bearing, not tidiness. ``func.date(timestamptz)`` resolves
    through the SESSION's timezone, so the pre-fix code is only wrong where the
    database runs in UTC — which Fly and the CI ``postgres:16`` container both
    do, and a developer's Asia/Tashkent Postgres does not. Without this pin the
    midnight proof below passes on a laptop while the bug it guards is live in
    production: the worst possible outcome for a regression test.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    # The URL OBJECT, never ``str(...)`` — that renders the password as ``***``.
    engine = create_async_engine(
        migrated_db.url, connect_args={"server_settings": {"timezone": "UTC"}}
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _contract(t, c, *, currency: str, monthly: Decimal, due: date):
    from app.models import Installment
    from app.models.enums import InstallmentStatus

    return Installment(
        tenant_id=t.id, customer_id=c.id, currency=currency,
        total_amount=monthly * 12, principal=monthly * 12, down_payment=Decimal("0"),
        months=12, monthly_amount=monthly, paid_amount=Decimal("0"),
        start_date=due - timedelta(days=30), next_due_date=due,
        status=InstallmentStatus.active,
    )


async def test_briefing_reports_each_currency_separately(migrated_db, monkeypatch) -> None:
    """A mixed shop is told both figures — never their meaningless sum."""
    from app.core.business_time import tashkent_today
    from app.core.documents import money, money_with_currency
    from app.models import Payment
    from app.models.enums import PaymentMethod, PaymentStatus
    from app.services import scheduler

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    today = tashkent_today()
    yesterday_noon = datetime.combine(
        today - timedelta(days=1), time(12, 0), tzinfo=TASHKENT
    ).astimezone(UTC)

    async with Session() as s:
        t, c = await _seed_owner(s, "mudd138-mixed", 138001)
        uzs = _contract(t, c, currency="UZS", monthly=Decimal("1200000"), due=today)
        usd = _contract(t, c, currency="USD", monthly=Decimal("350"), due=today)
        s.add_all([uzs, usd])
        await s.flush()
        # Yesterday's collections, one in each currency.
        s.add_all([
            Payment(tenant_id=t.id, installment_id=uzs.id, customer_id=c.id,
                    currency="UZS", amount=Decimal("500000"),
                    method=PaymentMethod.cash, status=PaymentStatus.approved,
                    paid_at=yesterday_noon, approved_at=yesterday_noon),
            Payment(tenant_id=t.id, installment_id=usd.id, customer_id=c.id,
                    currency="USD", amount=Decimal("40"),
                    method=PaymentMethod.cash, status=PaymentStatus.approved,
                    paid_at=yesterday_noon, approved_at=yesterday_noon),
        ])
        await s.commit()

    sent = _capture_telegram(monkeypatch)
    monkeypatch.setattr(scheduler, "SessionLocal", Session)
    result = await scheduler.daily_owner_briefing_job()

    assert result.get("sent") == 1, result
    text = sent[0]

    # Due today: the headcount still spans both contracts...
    assert "<b>2</b>" in text
    # ...but the money is reported once per currency.
    assert money_with_currency(Decimal("1200000"), "UZS", "uz") in text
    assert money_with_currency(Decimal("350"), "USD", "uz") in text
    assert money_with_currency(Decimal("500000"), "UZS", "uz") in text
    assert money_with_currency(Decimal("40"), "USD", "uz") in text
    # The cross-currency sums the old code printed appear nowhere.
    assert money(Decimal("1200350"), "UZS") not in text, "due-today summed UZS+USD"
    assert money(Decimal("500040"), "UZS") not in text, "collected summed UZS+USD"


async def test_briefing_counts_yesterday_by_tashkent_day_not_utc_date(
    migrated_db, monkeypatch
) -> None:
    """The 00:00–05:00 Tashkent window lands on the right side of midnight.

    Both payments below share the same UTC *date* (yesterday), which is exactly
    why ``func.date(...) == yesterday`` got both of them backwards.
    """
    from app.core.business_time import tashkent_today
    from app.core.documents import money_with_currency
    from app.models import Payment
    from app.models.enums import PaymentMethod, PaymentStatus
    from app.services import scheduler

    engine, Session = _utc_sessionmaker(migrated_db)
    today = tashkent_today()
    yesterday = today - timedelta(days=1)

    # 01:00 local on each day → 20:00 UTC on the day before. Both therefore
    # carry the UTC date `yesterday`, and only one of them IS yesterday.
    yesterday_early = datetime.combine(yesterday, time(1, 0), tzinfo=TASHKENT).astimezone(UTC)
    today_early = datetime.combine(today, time(1, 0), tzinfo=TASHKENT).astimezone(UTC)
    assert today_early.date() == yesterday, "premise: today's 01:00 is yesterday in UTC"
    assert yesterday_early.date() == yesterday - timedelta(days=1), \
        "premise: yesterday's 01:00 is the day-before-yesterday in UTC"

    async with Session() as s:
        t, c = await _seed_owner(s, "mudd138-midnight", 138002)
        inst = _contract(t, c, currency="UZS", monthly=Decimal("100000"), due=today)
        s.add(inst)
        await s.flush()
        s.add_all([
            # Belongs to yesterday's business day → MUST be counted.
            Payment(tenant_id=t.id, installment_id=inst.id, customer_id=c.id,
                    currency="UZS", amount=Decimal("700000"),
                    method=PaymentMethod.cash, status=PaymentStatus.approved,
                    paid_at=yesterday_early, approved_at=yesterday_early),
            # Belongs to TODAY's business day → must NOT be counted.
            Payment(tenant_id=t.id, installment_id=inst.id, customer_id=c.id,
                    currency="UZS", amount=Decimal("900000"),
                    method=PaymentMethod.cash, status=PaymentStatus.approved,
                    paid_at=today_early, approved_at=today_early),
        ])
        await s.commit()

    sent = _capture_telegram(monkeypatch)
    monkeypatch.setattr(scheduler, "SessionLocal", Session)
    try:
        await scheduler.daily_owner_briefing_job()
    finally:
        await engine.dispose()

    text = sent[0]
    assert money_with_currency(Decimal("700000"), "UZS", "uz") in text, \
        "yesterday's 01:00 Tashkent payment was dropped"
    assert money_with_currency(Decimal("900000"), "UZS", "uz") not in text, \
        "today's 01:00 Tashkent payment was booked to yesterday"
    assert money_with_currency(Decimal("1600000"), "UZS", "uz") not in text, \
        "both days were collapsed into one"
