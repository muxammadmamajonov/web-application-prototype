"""Every message carrying a live review action is eventually retired.

Two defects sat behind that sentence.

An `@channel` target was stored as the literal string `"@channel"` and then
skipped, so channel notifications were never tracked and their button stayed
live forever. Telegram's own response carries the canonical numeric
`msg.chat.id` — that is what must be recorded.

Retirement was scheduled with FastAPI BackgroundTasks, which is process-local,
and a single transient failure ended the attempt permanently. A restart, deploy
or brief Telegram outage therefore left a live-looking action after the money
decision was already final.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import (
    Customer,
    Installment,
    Payment,
    PaymentReviewNotice,
    Tenant,
    TenantUser,
    User,
)
from app.models.enums import PaymentMethod, PaymentStatus, TenantRole
from app.models.payment_review_notice import MAX_FINALIZE_ATTEMPTS


@pytest_asyncio.fixture
async def db():
    import os

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import Base

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
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await _reset()
        await engine.dispose()


async def _payment(sess) -> Payment:
    t = Tenant(name="Shop", slug=f"s-{uuid.uuid4().hex[:8]}")
    sess.add(t)
    await sess.flush()
    owner = User(telegram_id=770001)
    sess.add(owner)
    await sess.flush()
    sess.add(TenantUser(tenant_id=t.id, user_id=owner.id, role=TenantRole.owner))
    c = Customer(tenant_id=t.id, full_name="Aliyev Vali", phone="+998901112233")
    sess.add(c)
    await sess.flush()
    inst = Installment(
        tenant_id=t.id, customer_id=c.id, currency="UZS",
        contract_number="MDD-2026-000001", start_date=date(2026, 9, 1), months=6,
        monthly_amount=Decimal("1000000"), total_amount=Decimal("6000000"),
        principal=Decimal("6000000"), down_payment=Decimal("0"),
    )
    sess.add(inst)
    await sess.flush()
    pay = Payment(
        tenant_id=t.id, installment_id=inst.id, customer_id=c.id, currency="UZS",
        amount=Decimal("4800000"), method=PaymentMethod.cash,
        status=PaymentStatus.pending, paid_at=datetime(2026, 9, 1, 9, 23, tzinfo=UTC),
    )
    sess.add(pay)
    await sess.flush()
    return pay


async def _notice(
    sess, pay, *, chat=770001, message=11, state="pending", **kw,
) -> PaymentReviewNotice:
    n = PaymentReviewNotice(
        payment_id=pay.id, tenant_id=pay.tenant_id,
        chat_id=chat, message_id=message, state=state,
        body="📸 Yangi chek\n\nAliyev Vali 4 800 000 so'm\nShartnoma: MDD-2026-000001",
        **kw,
    )
    sess.add(n)
    await sess.flush()
    return n


# ── the send side: what gets tracked ────────────────────────────────────────

class _Chat:
    def __init__(self, cid: int) -> None:
        self.id = cid


class _Msg:
    def __init__(self, chat_id: int, message_id: int) -> None:
        self.chat = _Chat(chat_id)
        self.message_id = message_id


class _SendBot:
    """Resolves any target to a canonical numeric chat id, as Telegram does."""

    def __init__(self, resolved: dict, fail_first: Exception | None = None) -> None:
        self.resolved = resolved
        self.fail_first = fail_first
        self.calls = 0

    async def send_message(self, chat_id, **_kw):
        self.calls += 1
        if self.fail_first is not None and self.calls == 1:
            raise self.fail_first
        return _Msg(self.resolved[chat_id], 500 + self.calls)

    async def send_photo(self, chat_id, **kw):
        return await self.send_message(chat_id, **kw)


class TestWhatIsTracked:
    async def test_a_channel_handle_stores_the_canonical_numeric_id(self, monkeypatch):
        """THE defect: the request target was stored, so `@shopchannel` was kept
        as a string and then discarded as unusable."""
        import app.services.notifications as notif

        bot = _SendBot({"@shopchannel": -1001234567890})
        monkeypatch.setattr(notif, "get_bot", lambda: bot)
        track: list[tuple[int, int]] = []
        ok = await notif.send_to_telegram("@shopchannel", "hi", track=track)

        assert ok is True
        assert track == [(-1001234567890, 501)]
        assert all(isinstance(chat, int) for chat, _ in track)

    async def test_a_numeric_channel_id_is_stored(self, monkeypatch):
        import app.services.notifications as notif

        bot = _SendBot({-1009999: -1009999})
        monkeypatch.setattr(notif, "get_bot", lambda: bot)
        track: list[tuple[int, int]] = []
        await notif.send_to_telegram(-1009999, "hi", track=track)
        assert track == [(-1009999, 501)]

    async def test_an_owner_dm_is_stored(self, monkeypatch):
        import app.services.notifications as notif

        bot = _SendBot({770001: 770001})
        monkeypatch.setattr(notif, "get_bot", lambda: bot)
        track: list[tuple[int, int]] = []
        await notif.send_to_telegram(770001, "hi", track=track)
        assert track == [(770001, 501)]

    async def test_a_failed_send_tracks_nothing(self, monkeypatch):
        from aiogram.exceptions import TelegramAPIError

        import app.services.notifications as notif

        class _Dead:
            async def send_message(self, *a, **k):
                raise TelegramAPIError(method=None, message="chat not found")

        monkeypatch.setattr(notif, "get_bot", lambda: _Dead())
        track: list[tuple[int, int]] = []
        ok = await notif.send_to_telegram(770001, "hi", track=track)
        assert ok is False
        assert track == []

    async def test_a_flood_wait_retry_tracks_only_the_message_that_landed(
        self, monkeypatch,
    ):
        from aiogram.exceptions import TelegramRetryAfter

        import app.services.notifications as notif

        flood = TelegramRetryAfter(method=None, message="flood", retry_after=0)
        bot = _SendBot({770001: 770001}, fail_first=flood)
        monkeypatch.setattr(notif, "get_bot", lambda: bot)
        track: list[tuple[int, int]] = []
        ok = await notif.send_to_telegram(770001, "hi", track=track)

        assert ok is True
        assert len(track) == 1, "the failed attempt was tracked too"
        assert track[0] == (770001, 502)

    async def test_target_both_records_every_message(self, db, monkeypatch):
        """An owner DM and a channel are two live actions; both need a row."""
        import app.services.notifications as notif
        from app.models import TenantSettings
        from app.services.payment_review_notify import record_notices

        bot = _SendBot({770001: 770001, "@shopchannel": -1001234567890})
        monkeypatch.setattr(notif, "get_bot", lambda: bot)

        async with db() as sess:
            pay = await _payment(sess)
            sess.add(TenantSettings(
                tenant_id=pay.tenant_id, reminder_target="both",
                reminder_channel_id="@shopchannel",
            ))
            await sess.flush()

            track: list[tuple[int, int]] = []
            sent = await notif.notify_tenant_target(
                sess, tenant_id=pay.tenant_id, kind="receipt.uploaded",
                title="t", body="b", track=track,
            )
            stored = await record_notices(
                sess, payment_id=pay.id, tenant_id=pay.tenant_id, sent=track,
            )
            rows = (await sess.execute(select(PaymentReviewNotice))).scalars().all()

        assert sent == 2
        assert stored == 2
        assert {r.chat_id for r in rows} == {770001, -1001234567890}


# ── durability and retry ────────────────────────────────────────────────────

class _EditBot:
    def __init__(self, fail: Exception | None = None, fail_times: int = 0) -> None:
        self.edits: list[dict] = []
        self.fail = fail
        self.fail_times = fail_times
        self.calls = 0

    async def edit_message_text(self, **kw):
        self.calls += 1
        if self.fail is not None and self.calls <= (self.fail_times or 10**9):
            raise self.fail
        self.edits.append(kw)
        return True


def _use_bot(monkeypatch, bot):
    import app.services.notifications as notif
    monkeypatch.setattr(notif, "get_bot", lambda: bot)


class TestDurableScheduling:
    async def test_a_decision_marks_the_work_in_the_database(self, db):
        """Not a background task — a row. A restart delays retirement, it does
        not lose it."""
        from app.services.payment_review_notify import schedule_retirement

        async with db() as sess:
            pay = await _payment(sess)
            n = await _notice(sess, pay)
            count = await schedule_retirement(
                sess, payment_id=pay.id, outcome="approved",
            )
            await sess.flush()
            await sess.refresh(n)

        assert count == 1
        assert n.state == "retry"
        assert n.outcome == "approved"
        assert n.next_attempt_at is not None

    async def test_work_lost_to_a_restart_is_recovered_by_the_sweep(
        self, db, monkeypatch,
    ):
        """The immediate attempt never ran — the process died. The row is still
        marked due, so the sweep finds it."""
        from app.services.payment_review_notify import finalize_due, schedule_retirement

        bot = _EditBot()
        _use_bot(monkeypatch, bot)
        async with db() as sess:
            pay = await _payment(sess)
            n = await _notice(sess, pay)
            await schedule_retirement(sess, payment_id=pay.id, outcome="approved")
            await sess.flush()
            # no immediate attempt at all — the process "died" here
            done = await finalize_due(sess)
            await sess.refresh(n)

        assert done == 1
        assert n.state == "finalized"
        assert len(bot.edits) == 1

    async def test_an_expired_lease_is_reclaimed(self, db, monkeypatch):
        from app.services.payment_review_notify import finalize_due

        bot = _EditBot()
        _use_bot(monkeypatch, bot)
        async with db() as sess:
            pay = await _payment(sess)
            n = await _notice(
                sess, pay, state="finalizing", outcome="approved",
                lease_expires_at=datetime.now(UTC) - timedelta(minutes=5),
            )
            done = await finalize_due(sess)
            await sess.refresh(n)

        assert done == 1, "a lease abandoned by a dead worker was never reclaimed"
        assert n.state == "finalized"

    async def test_a_live_lease_is_not_stolen(self, db, monkeypatch):
        from app.services.payment_review_notify import finalize_due

        bot = _EditBot()
        _use_bot(monkeypatch, bot)
        async with db() as sess:
            pay = await _payment(sess)
            await _notice(
                sess, pay, state="finalizing", outcome="approved",
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
            done = await finalize_due(sess)

        assert done == 0
        assert bot.edits == []

    async def test_two_workers_do_not_edit_the_same_notice(self, db, monkeypatch):
        """A real race across two committed sessions. SKIP LOCKED means the
        second worker passes the row over rather than waiting for it."""
        from app.services.payment_review_notify import claim_due

        async with db() as setup:
            pay = await _payment(setup)
            await _notice(setup, pay, state="retry", outcome="approved",
                          next_attempt_at=datetime.now(UTC))
            await setup.commit()

        from sqlalchemy import text as _text

        async with db() as a, db() as b:
            first = await claim_due(a, limit=10)
            # A short lock_timeout so the assertion FAILS rather than hangs if
            # SKIP LOCKED is ever removed: a plain FOR UPDATE would block here
            # until the first worker commits.
            await b.execute(_text("SET LOCAL lock_timeout = '2s'"))
            try:
                second = await claim_due(b, limit=10)
            except Exception as exc:
                second = f"blocked: {type(exc).__name__}"
            await a.commit()
            await b.rollback()

        assert len(first) == 1
        assert second == [], (
            f"a second worker did not skip a held notice (got {second!r})"
        )


class TestRetryPolicy:
    async def test_a_transient_failure_is_retryable(self, db, monkeypatch):
        """The old code marked the first failure permanently stale."""
        from aiogram.exceptions import TelegramAPIError

        from app.services.payment_review_notify import attempt_finalize

        _use_bot(monkeypatch, _EditBot(
            fail=TelegramAPIError(method=None, message="Bad Gateway"),
        ))
        async with db() as sess:
            pay = await _payment(sess)
            n = await _notice(sess, pay, state="finalizing", outcome="approved")
            ok = await attempt_finalize(sess, n)

        assert ok is False
        assert n.state == "retry", "a transient failure ended the attempt"
        assert n.attempt_count == 1
        assert n.next_attempt_at > datetime.now(UTC)
        assert n.error and len(n.error) <= 64

    async def test_a_retry_after_is_retryable(self, db, monkeypatch):
        from aiogram.exceptions import TelegramRetryAfter

        from app.services.payment_review_notify import attempt_finalize

        _use_bot(monkeypatch, _EditBot(
            fail=TelegramRetryAfter(method=None, message="flood", retry_after=30),
        ))
        async with db() as sess:
            pay = await _payment(sess)
            n = await _notice(sess, pay, state="finalizing", outcome="approved")
            await attempt_finalize(sess, n)
        assert n.state == "retry"

    async def test_backoff_grows_and_is_capped(self):
        from app.services.payment_review_notify import (
            BACKOFF_CAP_SECONDS,
            _backoff,
        )

        first = _backoff(1).total_seconds()
        later = _backoff(5).total_seconds()
        assert later > first
        assert _backoff(50).total_seconds() <= BACKOFF_CAP_SECONDS

    async def test_backoff_carries_jitter(self):
        from app.services.payment_review_notify import _backoff

        samples = {_backoff(3).total_seconds() for _ in range(20)}
        assert len(samples) > 1, "no jitter — a fleet would retry in lockstep"

    async def test_a_permanent_failure_reaches_the_terminal_state(
        self, db, monkeypatch,
    ):
        from aiogram.exceptions import TelegramBadRequest

        from app.services.payment_review_notify import attempt_finalize

        _use_bot(monkeypatch, _EditBot(
            fail=TelegramBadRequest(method=None, message="message to edit not found"),
        ))
        async with db() as sess:
            pay = await _payment(sess)
            n = await _notice(sess, pay, state="finalizing", outcome="approved")
            await attempt_finalize(sess, n)

        assert n.state == "abandoned"
        assert n.attempt_count == 1, "a permanent condition burned the whole budget"

    async def test_the_retry_budget_is_bounded(self, db, monkeypatch):
        from aiogram.exceptions import TelegramAPIError

        from app.services.payment_review_notify import attempt_finalize

        _use_bot(monkeypatch, _EditBot(
            fail=TelegramAPIError(method=None, message="Bad Gateway"),
        ))
        async with db() as sess:
            pay = await _payment(sess)
            n = await _notice(sess, pay, state="finalizing", outcome="approved")
            for _ in range(MAX_FINALIZE_ATTEMPTS):
                n.state = "finalizing"
                await attempt_finalize(sess, n)

        assert n.state == "abandoned"
        assert n.attempt_count == MAX_FINALIZE_ATTEMPTS

    async def test_an_abandoned_notice_stays_observable(self, db, monkeypatch):
        from aiogram.exceptions import TelegramBadRequest

        from app.services.payment_review_notify import attempt_finalize

        _use_bot(monkeypatch, _EditBot(
            fail=TelegramBadRequest(method=None, message="bot was kicked"),
        ))
        async with db() as sess:
            pay = await _payment(sess)
            n = await _notice(sess, pay, state="finalizing", outcome="approved")
            await attempt_finalize(sess, n)
            rows = (await sess.execute(
                select(PaymentReviewNotice).where(
                    PaymentReviewNotice.state == "abandoned")
            )).scalars().all()

        assert len(rows) == 1
        assert rows[0].error


class TestTheRetiredMessage:
    async def test_it_keeps_the_receipt_context_and_states_the_outcome(
        self, db, monkeypatch,
    ):
        """Replacing the whole message with a bare status erases the customer,
        contract and amount a reader needs."""
        from app.services.payment_review_notify import attempt_finalize

        bot = _EditBot()
        _use_bot(monkeypatch, bot)
        async with db() as sess:
            pay = await _payment(sess)
            n = await _notice(sess, pay, state="finalizing", outcome="approved")
            await attempt_finalize(sess, n)

        text = bot.edits[0]["text"]
        assert "MDD-2026-000001" in text
        assert "Aliyev Vali" in text
        assert "4 800 000" in text
        assert "✅ Tasdiqlandi" in text

    async def test_it_carries_no_inline_action(self, db, monkeypatch):
        from app.services.payment_review_notify import attempt_finalize

        bot = _EditBot()
        _use_bot(monkeypatch, bot)
        async with db() as sess:
            pay = await _payment(sess)
            n = await _notice(sess, pay, state="finalizing", outcome="approved")
            await attempt_finalize(sess, n)

        assert bot.edits[0]["reply_markup"] is None
        assert "tekshirish" not in bot.edits[0]["text"].lower()

    async def test_a_russian_shop_reads_a_russian_outcome(self, db, monkeypatch):
        from app.services.payment_review_notify import attempt_finalize

        bot = _EditBot()
        _use_bot(monkeypatch, bot)
        async with db() as sess:
            pay = await _payment(sess)
            n = await _notice(sess, pay, state="finalizing",
                              outcome="rejected", locale="ru")
            await attempt_finalize(sess, n)
        assert "❌ Отклонён" in bot.edits[0]["text"]

    async def test_a_notice_with_no_stored_body_still_states_the_outcome(
        self, db, monkeypatch,
    ):
        from app.services.payment_review_notify import attempt_finalize

        bot = _EditBot()
        _use_bot(monkeypatch, bot)
        async with db() as sess:
            pay = await _payment(sess)
            n = PaymentReviewNotice(
                payment_id=pay.id, tenant_id=pay.tenant_id, chat_id=1, message_id=2,
                state="finalizing", outcome="approved",
            )
            sess.add(n)
            await sess.flush()
            await attempt_finalize(sess, n)
        assert bot.edits[0]["text"] == "✅ Tasdiqlandi"


class TestNothingLeaks:
    async def test_the_stored_error_is_only_a_class_name(self, db, monkeypatch):
        from aiogram.exceptions import TelegramAPIError

        from app.services.payment_review_notify import attempt_finalize

        _use_bot(monkeypatch, _EditBot(fail=TelegramAPIError(
            method=None,
            message="failed for https://muddato.uz/api/receipts/x?s=SECRETSIG "
                    "user +998901112233 initData=abc",
        )))
        async with db() as sess:
            pay = await _payment(sess)
            n = await _notice(sess, pay, state="finalizing", outcome="approved")
            await attempt_finalize(sess, n)

        stored = str(n.error)
        for leak in ("SECRETSIG", "receipts", "998901112233", "initData"):
            assert leak not in stored
        assert stored == "TelegramAPIError"

    async def test_a_notice_row_holds_no_capability(self, db):
        async with db() as sess:
            pay = await _payment(sess)
            n = await _notice(sess, pay)
            blob = " ".join(str(getattr(n, c.name)) for c in n.__table__.columns)
        for leak in ("?s=", "initData", "startapp", "Bearer"):
            assert leak not in blob


class TestIdempotenceAndScope:
    """Carried over from the suite this file supersedes.

    That suite also asserted an `@channel` target was deliberately skipped —
    the defect corrected here — so it is replaced rather than extended.
    """

    async def test_the_same_message_is_never_recorded_twice(self, db):
        from app.services.payment_review_notify import record_notices

        async with db() as sess:
            pay = await _payment(sess)
            first = await record_notices(sess, payment_id=pay.id,
                                         tenant_id=pay.tenant_id, sent=[(770001, 55)])
            again = await record_notices(sess, payment_id=pay.id,
                                         tenant_id=pay.tenant_id, sent=[(770001, 55)])
        assert first == 1
        assert again == 0

    async def test_a_malformed_reference_is_dropped_not_raised(self, db):
        from app.services.payment_review_notify import record_notices

        async with db() as sess:
            pay = await _payment(sess)
            stored = await record_notices(
                sess, payment_id=pay.id, tenant_id=pay.tenant_id,
                sent=[("not-a-chat", 5)],   # type: ignore[list-item]
            )
        assert stored == 0

    async def test_a_second_decision_retires_nothing_further(self, db, monkeypatch):
        from app.services.payment_review_notify import finalize_due, schedule_retirement

        bot = _EditBot()
        _use_bot(monkeypatch, bot)
        async with db() as sess:
            pay = await _payment(sess)
            await _notice(sess, pay)
            await schedule_retirement(sess, payment_id=pay.id, outcome="approved")
            await sess.flush()
            await finalize_due(sess)
            # The payment cannot transition twice, but even if the machinery is
            # driven again there is nothing left in a claimable state.
            second = await schedule_retirement(sess, payment_id=pay.id,
                                               outcome="approved")
            again = await finalize_due(sess)

        assert second == 0
        assert again == 0
        assert len(bot.edits) == 1

    async def test_a_guessed_payment_id_retires_no_message(self, db, monkeypatch):
        """The chat/message pair is looked up BY payment and never supplied, so
        inventing a UUID reaches nothing."""
        from app.services.payment_review_notify import schedule_retirement

        bot = _EditBot()
        _use_bot(monkeypatch, bot)
        async with db() as sess:
            pay = await _payment(sess)
            await _notice(sess, pay)
            count = await schedule_retirement(
                sess, payment_id=uuid.uuid4(), outcome="approved",
            )
        assert count == 0
        assert bot.edits == []

    async def test_an_unexpected_non_telegram_error_is_contained(self, db, monkeypatch):
        """The decision has committed; nothing this step raises may escape."""
        from app.services.payment_review_notify import attempt_finalize

        _use_bot(monkeypatch, _EditBot(fail=RuntimeError("aiogram changed shape")))
        async with db() as sess:
            pay = await _payment(sess)
            pay.status = PaymentStatus.approved
            await sess.flush()
            n = await _notice(sess, pay, state="finalizing", outcome="approved")
            ok = await attempt_finalize(sess, n)
            refreshed = await sess.get(Payment, pay.id)

        assert ok is False
        assert n.state == "retry"          # unrecognised → retryable, not abandoned
        assert refreshed.status == PaymentStatus.approved
