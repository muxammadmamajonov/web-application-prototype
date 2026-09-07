"""MUDD-136 — end-to-end proof for the reminder cron's reliability properties.

Three things this issue explicitly asked to be PROVEN, not read off the code:

1. A customer's OWN locale picks the reminder language (previously every
   customer got Uzbek regardless of `User.locale`, even though the recipient
   User — and its locale — was already resolved in the send path).
2. The cron is idempotent: a second run the same day must not re-send.
3. A customer reachable by NO channel (no linked Telegram account, no usable
   SMS fallback) used to leave zero trace anywhere. It must now be counted
   and logged so an operator can see it.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.business_time import tashkent_today
from app.services.scheduler import (
    _DEFAULTS_RU,
    _DEFAULTS_UZ,
    _default_template,
    _default_title,
    default_reminder_templates,
)

# ── unit: the shipped RU defaults are as sound as the UZ ones ──────────────

def test_ru_defaults_have_exactly_the_same_customer_facing_keys_as_uz() -> None:
    """A one-sided `key in _DEFAULTS_UZ` check (parametrized over RU's own
    keys) cannot see a key that UZ has and RU is missing — whoever adds a
    fifth CUSTOMER-facing template to UZ only would pass silently.

    `owner_overdue_alert` is deliberately excluded (reviewer LOW-5, second
    pass): it is never rendered through a real recipient locale — the owner
    fan-out has no single `User.locale` to pick — so the RU copy that used to
    sit there was coverage nothing could reach. It was removed from
    `_DEFAULTS_RU` rather than kept as dead weight; UZ still has it (the
    locale-less default `_default_template("owner_overdue_alert")` call
    resolves it).
    """
    from app.services.scheduler import _CUSTOMER_TEMPLATE_KEYS

    assert "owner_overdue_alert" not in _DEFAULTS_RU
    assert "owner_overdue_alert" in _DEFAULTS_UZ
    assert set(_DEFAULTS_RU) == _CUSTOMER_TEMPLATE_KEYS == (set(_DEFAULTS_UZ) - {"owner_overdue_alert"}), (
        f"UZ/RU customer-facing template keys diverged: "
        f"uz-only={set(_DEFAULTS_UZ) - {'owner_overdue_alert'} - set(_DEFAULTS_RU)!r}, "
        f"ru-only={set(_DEFAULTS_RU) - (set(_DEFAULTS_UZ) - {'owner_overdue_alert'})!r}"
    )


def test_titles_and_templates_cover_the_same_locales() -> None:
    """`_TITLES_BY_LOCALE` and `_TEMPLATES_BY_LOCALE` are independent dicts;
    nothing stops them covering different locale sets. The day a third locale
    is added to `SUPPORTED_LOCALES` and only one dict is updated, a recipient
    in it gets a title in one language and a body in another — HIGH-1's exact
    shape, silently, because both fall back to "uz" independently. Asserted
    at import time in `scheduler.py` too; this test pins it down explicitly
    rather than relying only on collection failing if the module can't import."""
    from app.core.documents import SUPPORTED_LOCALES
    from app.services.scheduler import _TEMPLATES_BY_LOCALE, _TITLES_BY_LOCALE

    assert set(_TITLES_BY_LOCALE) == set(_TEMPLATES_BY_LOCALE) == set(SUPPORTED_LOCALES)


@pytest.mark.parametrize("key", sorted(_DEFAULTS_RU))
def test_no_ru_default_template_leaves_a_placeholder(key: str) -> None:
    from app.services.notifications import reminder_context, render

    ctx = reminder_context(
        customer_name="Иван", currency="USD", amount=Decimal("4800000.00"),
        due_date="2026-09-15", contract_number="MDD-2026-000123",
        remaining=Decimal("14400000.00"), days_overdue=0, days_until_due=3,
        phone="+998901234567",
    )
    text = render(_DEFAULTS_RU[key], **ctx)
    assert "{" not in text and "}" not in text, (
        f"RU template {key!r} rendered with an unresolved placeholder: {text!r}"
    )


@pytest.mark.parametrize("key", sorted(_DEFAULTS_RU))
def test_no_ru_default_template_hardcodes_a_currency(key: str) -> None:
    tpl = _DEFAULTS_RU[key]
    assert "UZS" not in tpl, f"RU template {key!r} hardcodes a currency: {tpl!r}"
    if "{amount}" in tpl or "{remaining}" in tpl:
        assert "{currency}" in tpl, f"RU template {key!r} shows money with no currency"


@pytest.mark.parametrize(("locale", "expect_ru"), [("ru", True), ("uz", False), ("ru-RU", True), (None, False), ("fr", False)])
def test_default_template_picks_locale(locale, expect_ru) -> None:
    text = _default_template("due_today", locale) if locale is not None else _default_template("due_today")
    is_cyrillic = any("а" <= ch <= "я" or ch in "Ёё" for ch in text.lower())
    assert is_cyrillic is expect_ru, f"locale={locale!r} got {text!r}"


@pytest.mark.parametrize(("locale", "expect_ru"), [("ru", True), ("uz", False), ("ru-RU", True), (None, False), ("fr", False)])
def test_default_title_picks_locale(locale, expect_ru) -> None:
    """The customer-facing title used to be a hardcoded UZ literal even when
    the body was correctly localized, so a RU recipient got a bilingual
    message. Title must follow `recipient_locale` exactly like the body."""
    title = _default_title(locale) if locale is not None else _default_title()
    is_cyrillic = any("а" <= ch <= "я" or ch in "Ёё" for ch in title.lower())
    assert is_cyrillic is expect_ru, f"locale={locale!r} got {title!r}"


def test_default_reminder_templates_locale_param_is_backward_compatible() -> None:
    """Existing callers (admin preview, tenant-settings seed) call this with
    no argument at all — the new `locale` param must not break them."""
    assert default_reminder_templates() == dict(_DEFAULTS_UZ)
    assert default_reminder_templates("ru") == dict(_DEFAULTS_RU)


# ── integration: real Postgres, real send path ─────────────────────────────

async def _seed(Session, *, slug: str, customer_telegram_id: int | None,
                 recipient_locale: str | None = None, offset_days: int = 0):
    """A tenant + customer + one installment due `offset_days` from today.

    ``customer_telegram_id`` None models a customer who never linked Telegram.
    When it is set and ``recipient_locale`` is given, a matching User row is
    created so the send path can resolve a recipient + locale.
    """
    from app.models import Customer, Installment, Tenant, User
    from app.models.enums import InstallmentStatus

    # The job classifies due/overdue on `tashkent_today()`, not the runner's
    # local clock — seeding off `date.today()` agrees with it only on a
    # UTC+5 runner and silently disagrees 19:00-24:00 UTC everywhere else.
    today = tashkent_today()
    async with Session() as s:
        t = Tenant(slug=slug, name=slug)
        s.add(t)
        await s.flush()
        c = Customer(
            tenant_id=t.id, full_name="Test Customer", phone="+998901112200",
            telegram_id=customer_telegram_id,
        )
        s.add(c)
        if customer_telegram_id is not None and recipient_locale is not None:
            s.add(User(telegram_id=customer_telegram_id, phone="+998901112200", locale=recipient_locale))
        await s.flush()
        due = today + timedelta(days=offset_days)
        status = InstallmentStatus.overdue if offset_days < 0 else InstallmentStatus.active
        inst = Installment(
            currency="UZS", tenant_id=t.id, customer_id=c.id,
            total_amount=Decimal("1000000"), principal=Decimal("1000000"),
            down_payment=Decimal("0"), months=10, monthly_amount=Decimal("100000"),
            paid_amount=Decimal("0"), start_date=today - timedelta(days=30),
            status=status, next_due_date=due, contract_number=f"C-{slug}",
        )
        s.add(inst)
        await s.flush()
        ids = (t.id, c.id, inst.id)
        await s.commit()
    return ids


async def test_reminder_uses_recipient_locale_and_is_idempotent(migrated_db, monkeypatch) -> None:
    from app.services import notifications, scheduler

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    await _seed(
        Session, slug="mudd136-ru", customer_telegram_id=555001, recipient_locale="ru",
        offset_days=0,  # due_today
    )

    sent: list[tuple[int, str]] = []

    async def _fake_send(chat_id, text, **kwargs):
        sent.append((chat_id, text))
        return True

    monkeypatch.setattr(notifications, "send_to_telegram", _fake_send)
    monkeypatch.setattr(scheduler, "SessionLocal", Session)

    # `.__wrapped__` (set by `functools.wraps` in `single_instance`) calls the
    # undecorated body directly, bypassing the cron lock. The lock's own
    # 6-hour hold-after-success (F-09) is intentional and covered separately
    # (test_f09_cron_lease.py) — it would otherwise make BOTH calls below race
    # against the first call's still-held lease rather than exercising the
    # dedup guard this test is actually about.
    job = scheduler.send_reminders_job.__wrapped__

    result = await job()
    assert result["sent_customer"] == 1, result
    assert len(sent) == 1
    _, text = sent[0]
    # Russian recipient must get an all-Russian message — title AND body.
    # A single "Bugun" check on the body alone would still pass if the
    # title were left as the hardcoded UZ literal (HIGH-1): a Latin-script
    # UZ-only word check is what pins that regression down.
    assert "Сегодня" in text, f"expected Russian body, got: {text!r}"
    for uz_only_word in ("Bugun", "To'lov eslatmasi"):
        assert uz_only_word not in text, f"leaked UZ copy {uz_only_word!r} in: {text!r}"

    # A same-day re-run must NOT re-send — the dedup guard on a delivered
    # Notification row is the idempotency contract this whole cron relies on.
    result2 = await job()
    assert result2["sent_customer"] == 0, result2
    assert result2["skipped_dup"] == 1, result2
    assert len(sent) == 1, "a second run must not call the provider again"


async def test_customer_with_no_channel_is_counted_not_silently_dropped(migrated_db, monkeypatch) -> None:
    """No linked Telegram user + no SMS provider configured (the default in
    tests) → previously zero record anywhere. Must now show up as
    `unreachable` in the job's own return value."""
    from app.models import Notification, SmsMessage
    from app.services import scheduler

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    _tid, _cid, iid = await _seed(
        Session, slug="mudd136-unreach", customer_telegram_id=None, offset_days=0,
    )

    monkeypatch.setattr(scheduler, "SessionLocal", Session)
    result = await scheduler.send_reminders_job()

    assert result["sent_customer"] == 0, result
    assert result["sent_sms"] == 0, result
    assert result["unreachable"] == 1, result

    async with Session() as s:
        notif_count = len((await s.execute(
            select(Notification.id).where(Notification.installment_id == iid)
        )).all())
        sms_count = len((await s.execute(
            select(SmsMessage.id).where(SmsMessage.installment_id == iid)
        )).all())
    # Confirms the gap this test exists to close: no delivery row of ANY kind
    # is created for this customer — `unreachable` is the only surviving signal.
    assert notif_count == 0
    assert sms_count == 0


async def test_partial_payment_recomputes_remaining_amount_and_days(migrated_db, monkeypatch) -> None:
    """Requirement 2: a customer's payment must be reflected in the NEXT
    rendered reminder — recomputed remaining (with its currency) and correct
    due/overdue state — verified by executing a REAL payment through
    ``app.services.installments.apply_payment``, the exact function every
    webhook / cash-approval site uses, not by setting ``paid_amount`` by hand
    (which is "reasoning about the code", the one thing the issue explicitly
    ruled out).

    The payment is deliberately LESS than one `monthly_amount`: a full-period
    payment advances `next_due_date` by a calendar month via `add_months`
    (~28-31 days), which would push the contract outside the cron's own
    7-day `_reminders_base` envelope and make it invisible to the very next
    run — not a bug, just outside this test's scope. A sub-monthly payment
    keeps the due date (and therefore the reminder `kind`) unchanged, so it
    isolates exactly what requirement 2 asks for: does the render pick up
    the fresh ``paid_amount``, not "did the schedule also move."
    """
    from app.models import Customer, Installment, Tenant, User
    from app.models.enums import InstallmentStatus
    from app.services import notifications, scheduler
    from app.services.installments import add_months, apply_payment, recompute_state

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    today = tashkent_today()
    now = datetime.combine(today, time(12), tzinfo=UTC)

    # Anchored via the SAME `add_months` the production recompute uses, so
    # the installment starts out overdue by exactly 3 days — not a magic
    # date, the inverse of the real formula.
    start_date = add_months(today - timedelta(days=3), -1)

    async with Session() as s:
        t = Tenant(slug="mudd136-partial", name="mudd136-partial")
        s.add(t)
        await s.flush()
        c = Customer(
            tenant_id=t.id, full_name="Test Customer", phone="+998901112200",
            telegram_id=555002,
        )
        s.add(c)
        s.add(User(telegram_id=555002, phone="+998901112200", locale="ru"))
        await s.flush()
        inst = Installment(
            currency="UZS", tenant_id=t.id, customer_id=c.id,
            total_amount=Decimal("1000000"), principal=Decimal("1000000"),
            down_payment=Decimal("0"), months=10, monthly_amount=Decimal("100000"),
            paid_amount=Decimal("0"), start_date=start_date,
            status=InstallmentStatus.active, contract_number="C-partial",
        )
        s.add(inst)
        await s.flush()
        recompute_state(inst, now=now)  # derives next_due_date the same way production does
        assert (inst.next_due_date - today).days == -3, inst.next_due_date
        iid = inst.id
        await s.commit()

    # The REAL partial-payment path — not `inst.paid_amount = ...`. 40,000 is
    # less than the 100,000 monthly_amount, so `recompute_state` correctly
    # leaves the schedule untouched (months_paid stays 0): the contract is
    # STILL exactly 3 days overdue, which is the correct state, not a bug.
    async with Session() as s:
        inst = await s.get(Installment, iid)
        assert inst is not None
        apply_payment(inst, Decimal("40000"), now=now)
        await s.commit()
        # Read back what the real function computed — the assertions below
        # check the RENDERED message against this, not a hand-picked number.
        expected_remaining = inst.remaining_balance
        expected_due = inst.next_due_date
        expected_status = inst.status

    assert expected_remaining == Decimal("960000"), expected_remaining
    assert expected_due == today - timedelta(days=3), (
        "a sub-monthly payment must not move the schedule"
    )
    assert expected_status == InstallmentStatus.overdue

    sent: list[str] = []

    async def _fake_send(chat_id, text, **kwargs):
        sent.append(text)
        return True

    monkeypatch.setattr(notifications, "send_to_telegram", _fake_send)
    monkeypatch.setattr(scheduler, "SessionLocal", Session)
    job = scheduler.send_reminders_job.__wrapped__

    result = await job()
    assert result["sent_customer"] == 1, result
    assert len(sent) == 1
    text = sent[0]

    # Recomputed remaining, WITH currency — 960,000, not the original 1,000,000.
    assert "960 000" in text, f"recomputed remaining not in: {text!r}"
    assert "1 000 000" not in text, f"stale pre-payment remaining leaked into: {text!r}"
    assert "UZS" in text
    # Correct (unchanged) overdue state: still 3 days, not reset to 0 by the
    # payment and not silently wrong in the other direction either.
    assert "3 дн." in text, f"recomputed days_overdue not in: {text!r}"
    # Locale survives the recompute.
    assert "Здравствуйте" in text


async def test_failed_deliveries_recorded_in_audit_log(migrated_db, monkeypatch) -> None:
    """Requirement 7: Every unreachable customer and every permanently failed
    send must be recorded in the audit log, visible at GET /platform/audit,
    NOT just a Fly log line. This test verifies the audit.record() call
    actually creates rows."""
    from app.models import AuditLog
    from app.services import scheduler

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    tid, cid, iid = await _seed(
        Session, slug="mudd136-audit", customer_telegram_id=None, offset_days=0,
    )

    monkeypatch.setattr(scheduler, "SessionLocal", Session)
    result = await scheduler.send_reminders_job()

    # Verify the job counted this as unreachable
    assert result["unreachable"] == 1, result

    # Verify an audit log row was created
    async with Session() as s:
        audit_rows = (await s.execute(
            select(AuditLog).where(
                (AuditLog.action == "reminder.delivery_failed") &
                (AuditLog.tenant_id == tid)
            )
        )).scalars().all()

    assert len(audit_rows) >= 1, "audit.record() was not called or row was not persisted"
    row = audit_rows[0]
    # Verify the row has the IDs (never PII)
    assert row.entity_type == "installment"
    assert row.entity_id == str(iid)
    after_data = row.after_json or {}
    assert after_data.get("customer_id") == str(cid)
    assert after_data.get("reason") == "no_channel"


async def test_backoff_retry_is_inside_dedup_guard_and_bounded(migrated_db, monkeypatch) -> None:
    """Requirement 5: Transient failures (timeouts, 5xx) are retried with
    bounded exponential backoff, BUT only inside the per-installment dedup
    guard — retries never re-enter it. A failed send is retried at most once
    per recipient (two attempts total) and the job has a global budget to
    prevent a fleet-wide outage from turning bounded retries into unbounded
    ones."""
    from app.services import notifications, scheduler

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    _tid, _cid, iid = await _seed(
        Session, slug="mudd136-retry", customer_telegram_id=555003,
        recipient_locale="uz", offset_days=0,
    )

    attempt_count = {"value": 0}
    sent_successfully = {"value": False}

    async def _fake_send_transient_then_ok(chat_id, text, **kwargs):
        """First attempt times out (transient), second attempt succeeds."""
        attempt_count["value"] += 1
        if attempt_count["value"] == 1:
            # Simulate a transient failure (timeout)
            return False
        else:
            # Second attempt succeeds
            sent_successfully["value"] = True
            return True

    def _sms_fallback_must_not_be_tried(*args, **kwargs):
        raise AssertionError(
            "SMS fallback was attempted — the Telegram retry must succeed "
            "(or exhaust its OWN bound) BEFORE the caller ever falls through "
            "to a different channel, otherwise the retry is not really "
            "'inside' the send, it's just a slower way to reach the same "
            "SMS fallback every single failure already reaches."
        )

    monkeypatch.setattr(notifications, "send_to_telegram", _fake_send_transient_then_ok)
    monkeypatch.setattr(scheduler, "SessionLocal", Session)
    monkeypatch.setattr(scheduler, "_try_sms_fallback", _sms_fallback_must_not_be_tried)

    job = scheduler.send_reminders_job.__wrapped__
    result = await job()

    # Backoff should have retried and succeeded
    assert result["sent_customer"] == 1, (
        f"backoff retry did not succeed: {result}"
    )
    assert attempt_count["value"] == 2, (
        f"expected exactly 2 attempts (1 + 1 retry), got {attempt_count['value']}"
    )
    assert sent_successfully["value"], "second attempt was not reached"

    # Idempotency invariant even with a retry: `retry_existing=True` reuses
    # the SAME Notification row across attempts, so a retried send is still
    # exactly one row for (tenant, kind, installment, day) — not two.
    async with Session() as s:
        from app.models import Notification
        rows = (await s.execute(
            select(Notification).where(Notification.installment_id == iid)
        )).scalars().all()
    assert len(rows) == 1, f"retry produced {len(rows)} Notification rows, expected 1"
    assert rows[0].attempt_count == 2, rows[0].attempt_count


async def test_backoff_retry_is_bounded_per_recipient(migrated_db, monkeypatch) -> None:
    """The other half of requirement 5's bound: a recipient whose Telegram
    send NEVER succeeds must not be retried forever — at most one retry (two
    attempts total), then fall through to SMS/`unreachable` like before this
    change existed. Mutation-style: if the per-recipient cap were removed
    (or set higher), this would hang or over-count attempts instead of
    completing with exactly 2.
    """
    from app.services import notifications, scheduler

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    await _seed(
        Session, slug="mudd136-retry-bounded", customer_telegram_id=555004,
        recipient_locale="uz", offset_days=0,
    )

    attempt_count = {"value": 0}

    async def _always_fails(chat_id, text, **kwargs):
        attempt_count["value"] += 1
        return False

    monkeypatch.setattr(notifications, "send_to_telegram", _always_fails)
    monkeypatch.setattr(scheduler, "SessionLocal", Session)

    job = scheduler.send_reminders_job.__wrapped__
    result = await job()

    assert attempt_count["value"] == 2, (
        f"expected the per-recipient cap (2 attempts total), got {attempt_count['value']}"
    )
    assert result["sent_customer"] == 0, result
    # No SMS configured in this test (sms_eskiz.is_configured() is False by
    # default) → falls through to the requirement-7 failure-recording path.
    assert result["unreachable"] == 1, result


async def test_backoff_retry_stops_immediately_when_breaker_is_open(migrated_db, monkeypatch) -> None:
    """Reviewer MEDIUM-1: in a fleet-wide Telegram outage the #255 circuit
    breaker is OPEN, so `send_to_telegram` returns `False` before ever
    touching the network. Retrying that is a pure sleep — worse, the job's
    shared `retry_budget` is spent first-come, so breaker-open recipients
    early in a fan-out can exhaust it before a genuinely transient timeout
    later in the run gets its one retry. `_notify_recipient_with_backoff`
    must check `telegram_breaker.allow()` and skip the retry rather than
    sleeping and trying again."""
    from app.core.breaker import telegram_breaker
    from app.services import notifications, scheduler

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    await _seed(
        Session, slug="mudd136-breaker-open", customer_telegram_id=555005,
        recipient_locale="uz", offset_days=0,
    )

    attempt_count = {"value": 0}

    async def _always_fails(chat_id, text, **kwargs):
        # A real breaker-open `send_to_telegram` would also return False
        # without reaching this point — the fake stands in for that surface.
        attempt_count["value"] += 1
        return False

    monkeypatch.setattr(notifications, "send_to_telegram", _always_fails)
    monkeypatch.setattr(telegram_breaker, "allow", lambda: False)
    monkeypatch.setattr(scheduler, "SessionLocal", Session)

    job = scheduler.send_reminders_job.__wrapped__
    result = await job()

    assert attempt_count["value"] == 1, (
        f"breaker OPEN must skip the retry entirely (1 attempt, no sleep), "
        f"got {attempt_count['value']}"
    )
    assert result["sent_customer"] == 0, result
    assert result["unreachable"] == 1, result


async def test_sms_fallback_succeeds_when_telegram_unavailable(migrated_db, monkeypatch) -> None:
    """Requirement 1: The SMS success path is exercised — not just its dedup
    (test_f09_sms_dedup.py builds its own schema and never calls the real
    provider function). Telegram is unavailable (no linked User), so the
    cron falls back to SMS, which must actually succeed.

    Three preconditions the fallback checks, all real here: the provider
    must report configured (``sms_eskiz.is_configured``), the tenant must
    have SMS budget headroom (``TenantSettings.sms_budget_uzs_per_month``,
    which `_seed` never creates a row for — defaults to 0/disabled), and the
    provider call itself (``sms_eskiz.send_sms``, NOT
    ``notifications.send_sms`` — that name does not exist; patching it would
    silently no-op past `monkeypatch`'s own `raising=True` check) must
    return a successful ``SmsResult``.
    """
    from app.models import SmsMessage, TenantSettings
    from app.services import scheduler, sms_eskiz

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    tid, _cid, iid = await _seed(
        Session, slug="mudd136-sms", customer_telegram_id=None, offset_days=0,
    )
    async with Session() as s:
        s.add(TenantSettings(tenant_id=tid, sms_budget_uzs_per_month=1_000_000))
        await s.commit()

    sms_sent: list[tuple[str, str]] = []

    async def _fake_send_sms(phone: str, text: str, *, sender: str | None = None):
        sms_sent.append((phone, text))
        return sms_eskiz.SmsResult(ok=True, parts=1, cost_uzs=45, provider_message_id="test-1")

    monkeypatch.setattr(sms_eskiz, "is_configured", lambda: True)
    monkeypatch.setattr(sms_eskiz, "send_sms", _fake_send_sms)
    monkeypatch.setattr(scheduler, "SessionLocal", Session)

    job = scheduler.send_reminders_job.__wrapped__
    result = await job()

    # SMS should have been the fallback (no Telegram available)
    assert result["sent_sms"] == 1, result
    assert result["sent_customer"] == 0, result  # No Telegram
    assert result["unreachable"] == 0, result  # SMS reached them — NOT unreachable
    assert len(sms_sent) == 1, "SMS provider should have been called"

    # Verify the SMS was sent to the customer's phone
    phone, text = sms_sent[0]
    assert phone == "+998901112200", f"wrong phone: {phone}"
    # Text should contain the reminder content (use locale-free check)
    assert "100 000" in text or "100000" in text, f"amount not in SMS: {text!r}"

    # Verify SmsMessage row was created (different from Notification), and
    # finalised with the provider's real outcome, not left `pending`.
    async with Session() as s:
        sms_rows = (await s.execute(
            select(SmsMessage).where(SmsMessage.installment_id == iid)
        )).scalars().all()

    assert len(sms_rows) == 1, "SmsMessage row should have been created"
    from app.models.enums import SmsStatus
    assert sms_rows[0].status == SmsStatus.sent
    assert sms_rows[0].provider_message_id == "test-1"


async def test_reminder_dedup_survives_the_utc_tashkent_day_boundary(migrated_db, monkeypatch) -> None:
    """Regression: this suite caught a LIVE bug by accident, running at
    19:09 UTC while verifying this same second pass. The customer-facing
    dedup threshold was ``datetime.combine(today, min, tzinfo=UTC)`` where
    ``today = tashkent_today()`` — mislabeling a TASHKENT calendar date as if
    it were already a UTC midnight, five hours early.

    In the 19:00-24:00 UTC window (Tashkent has rolled to tomorrow; the UTC
    calendar date hasn't), that threshold lands in the FUTURE relative to a
    Notification committed moments earlier under the real UTC clock — so the
    dedup guard finds nothing and a same-day re-run double-sends. Deliberately
    deterministic regardless of when this test actually runs, unlike the
    end-to-end idempotency test above (correct, but only exercises this by
    chance, five hours a day): freezes `datetime.now(UTC)` INSIDE
    `app.core.business_time` itself. Patching only `scheduler.tashkent_today`
    is not enough — the dedup fix calls `tashkent_day_start_utc()`, a
    separate function that resolves its OWN `tashkent_today()` from
    `business_time`'s namespace, unaffected by patching the name `scheduler`
    imported. Freezing the clock the module's functions actually share is
    what makes `today`'s offset math and the dedup threshold agree, exactly
    as they must in production.
    """
    from app.core import business_time
    from app.models import Installment, Notification, User
    from app.services import scheduler

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    # 19:30 UTC on day N is 00:30 on day N+1 in Tashkent (UTC+5) — exactly the
    # window where `tashkent_today()` has rolled over but a real UTC
    # `func.now()` insert a moment earlier has not.
    frozen_instant = datetime(2026, 1, 1, 19, 30, tzinfo=UTC)
    frozen_today = date(2026, 1, 2)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_instant if tz is not None else frozen_instant.replace(tzinfo=None)

    monkeypatch.setattr(business_time, "datetime", _FrozenDatetime)
    assert business_time.tashkent_today() == frozen_today  # sanity: the freeze took

    tid, _cid, iid = await _seed(
        Session, slug="mudd136-tzbound", customer_telegram_id=555099,
        recipient_locale="uz", offset_days=0,
    )
    # `_seed` calls `tashkent_today()` too, so with the clock frozen it
    # already seeded `next_due_date == frozen_today` — no separate pin needed.
    async with Session() as s:
        inst = await s.get(Installment, iid)
        assert inst.next_due_date == frozen_today, inst.next_due_date
        recipient = (await s.execute(
            select(User).where(User.telegram_id == 555099)
        )).scalar_one()

    # The "already delivered a few hours ago" Notification — real UTC clock,
    # on the calendar date BEFORE the frozen Tashkent "today". This is
    # exactly the live failure shape: a genuine send from earlier today
    # (Tashkent), timestamped on yesterday's UTC date.
    earlier_utc = datetime.combine(frozen_today - timedelta(days=1), time(19, 30), tzinfo=UTC)
    async with Session() as s:
        s.add(Notification(
            user_id=recipient.id, tenant_id=tid, installment_id=iid,
            kind="reminder.due_today", title="x", body="x",
            created_at=earlier_utc, delivered_at=earlier_utc,
        ))
        await s.commit()

    monkeypatch.setattr(scheduler, "SessionLocal", Session)
    job = scheduler.send_reminders_job.__wrapped__
    result = await job()

    assert result["sent_customer"] == 0, (
        f"dedup guard missed an already-delivered reminder across the "
        f"UTC/Tashkent day boundary: {result}"
    )
    assert result["skipped_dup"] == 1, result


async def test_reminder_dedup_closes_the_earlier_same_utc_day_gap(migrated_db, monkeypatch) -> None:
    """The test above proves the SCHEDULER's own dedup query survives the
    UTC/Tashkent boundary. It does not prove the cron is idempotent in that
    window, because `notify_user(retry_existing=True)` — called from inside
    `_notify_recipient_with_backoff` — used to run its OWN, DIFFERENT dedup
    window: a plain UTC-midnight cutoff, never fixed alongside the scheduler's.

    The gap that opened: the scheduler's dedup asks "is there a delivered row
    since Tashkent-today's start (19:00 UTC)?" — correctly say NO for a row
    from the ORDINARY 09:00-Tashkent / 04:00-UTC run earlier the same
    Tashkent-yesterday. So the job proceeds to send. But `notify_user`'s old
    window asked "is there a delivered row since UTC-midnight?" — and 04:00
    UTC is on the SAME UTC calendar date as the frozen 19:30 UTC instant
    below, so it WRONGLY matched and reused that stale row (bumping
    `attempt_count`, never writing fresh title/body) instead of creating a
    new one. Every further run in the 19:00-24:00 UTC window then repeated
    the same wrong match — no dedup marker for TODAY'S Tashkent day was ever
    created, so `skipped_dup` never rose and the customer's Telegram got a
    real send on every single run.

    Reproduces the reviewer's live-Postgres probe exactly: one delivered row
    seeded at 2026-01-01 04:00 UTC (09:00 Tashkent the PREVIOUS Tashkent day),
    the job called three times with one shared frozen clock at
    2026-01-01 19:30 UTC (00:30 Tashkent the NEXT day). With both dedup
    windows aligned on `tashkent_day_start_utc()`, the first call must create
    exactly one fresh row and send once; the second and third must find that
    fresh row through the SAME query the scheduler already runs and skip —
    not silently re-match the stale row through a different one.
    """
    from app.core import business_time
    from app.models import Notification, User
    from app.services import notifications, scheduler

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    frozen_instant = datetime(2026, 1, 1, 19, 30, tzinfo=UTC)
    frozen_today = date(2026, 1, 2)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_instant if tz is not None else frozen_instant.replace(tzinfo=None)

    monkeypatch.setattr(business_time, "datetime", _FrozenDatetime)
    # `notifications.py` calls `datetime.now(UTC)` through ITS OWN imported
    # name, not through `business_time` — freezing only the latter (as the
    # test above does) would leave a regression in `notify_user`'s window
    # invisible: `datetime.now(UTC)` there would silently read the REAL wall
    # clock, so the stale row's 2026-01-01 timestamp could never collide with
    # whatever day the test happens to run on. Both names have to point at
    # the same frozen instant for this test to actually exercise the failure
    # mode it's named for.
    monkeypatch.setattr(notifications, "datetime", _FrozenDatetime)
    assert business_time.tashkent_today() == frozen_today  # sanity: the freeze took

    tid, _cid, iid = await _seed(
        Session, slug="mudd136-tzbound-gap", customer_telegram_id=555098,
        recipient_locale="uz", offset_days=0,
    )
    async with Session() as s:
        recipient = (await s.execute(
            select(User).where(User.telegram_id == 555098)
        )).scalar_one()

    # The ordinary 09:00-Tashkent run of the PREVIOUS Tashkent day, timestamped
    # 2026-01-01 04:00 UTC — the SAME UTC calendar date as `frozen_instant`
    # (2026-01-01 19:30 UTC), but a DIFFERENT Tashkent day. This is exactly
    # the row the old `notify_user` window (UTC midnight) could see and the
    # scheduler's own window (Tashkent day start) correctly could not.
    stale_utc = datetime(2026, 1, 1, 4, 0, tzinfo=UTC)
    async with Session() as s:
        s.add(Notification(
            user_id=recipient.id, tenant_id=tid, installment_id=iid,
            kind="reminder.due_today", title="x", body="YESTERDAY-BODY",
            created_at=stale_utc, delivered_at=stale_utc,
        ))
        await s.commit()

    provider_calls = {"value": 0}

    async def _fake_send(chat_id, text, **kwargs):
        provider_calls["value"] += 1
        return True

    monkeypatch.setattr(notifications, "send_to_telegram", _fake_send)
    monkeypatch.setattr(scheduler, "SessionLocal", Session)

    job = scheduler.send_reminders_job.__wrapped__
    results = [await job() for _ in range(3)]

    assert [r["sent_customer"] for r in results] == [1, 0, 0], (
        f"expected exactly one real send across the three runs: {results}"
    )
    assert [r["skipped_dup"] for r in results] == [0, 1, 1], (
        f"runs 2 and 3 must recognise run 1's fresh row as today's delivery: {results}"
    )
    assert provider_calls["value"] == 1, (
        f"the stale UTC-day row must not suppress a real Telegram send: "
        f"{provider_calls['value']} provider calls"
    )

    async with Session() as s:
        rows = (await s.execute(
            select(Notification)
            .where(Notification.installment_id == iid)
            .order_by(Notification.created_at)
        )).scalars().all()
    assert len(rows) == 2, f"expected the stale row plus exactly one fresh row: {len(rows)}"
    assert rows[0].body == "YESTERDAY-BODY" and rows[0].attempt_count in (0, None), (
        "the stale row must be left untouched, not reused"
    )
    assert rows[1].body != "YESTERDAY-BODY" and rows[1].attempt_count == 1, (
        f"the fresh row must carry today's content, not the reused stale body: {rows[1].body!r}"
    )
