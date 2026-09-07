"""Sending the receipt-review notice, and durably retiring its action.

INVARIANT: `Payment.status` is authoritative. Nothing here decides, repeats or
reverses a money outcome; a Telegram failure can never roll one back.

INVARIANT: retirement is durable. The decision marks the work in the same
transaction that commits it, so a restart or outage delays retirement rather
than losing it.

INVARIANT: a notice is claimed under a row lock with SKIP LOCKED, so two
workers never edit the same message.
"""
from __future__ import annotations

import logging
import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PaymentReviewNotice
from app.models.payment_review_notice import MAX_FINALIZE_ATTEMPTS

log = logging.getLogger("muddato.payment_review")

#: How long a worker may hold a claim before another may reclaim it. Longer than
#: the edit timeout, so an in-flight attempt is never duplicated.
LEASE_SECONDS = 120

#: Bounded exponential backoff. Jitter spreads a fleet's retries so a recovering
#: Telegram is not hit by every worker at the same instant.
BACKOFF_BASE_SECONDS = 30
BACKOFF_CAP_SECONDS = 3600

_FINAL_LABEL = {
    "uz": {"approved": "✅ Tasdiqlandi", "rejected": "❌ Rad etildi"},
    "ru": {"approved": "✅ Подтверждён", "rejected": "❌ Отклонён"},
}

#: Telegram conditions that will never succeed on a retry. Matched on the error
#: text because aiogram exposes no stable code for these.
_PERMANENT_FRAGMENTS = (
    "message to edit not found",
    "message can't be edited",
    "message is not modified",
    "chat not found",
    "bot was kicked",
    "bot is not a member",
    "not enough rights",
    "have no rights",
    "message_id_invalid",
)


def _backoff(
    attempt: int,
    *,
    base_seconds: float = BACKOFF_BASE_SECONDS,
    cap_seconds: float = BACKOFF_CAP_SECONDS,
) -> timedelta:
    """Delay before attempt ``attempt`` (1-based), capped, with jitter.

    ``base_seconds``/``cap_seconds`` default to this module's own sweep-job
    scale (30s-3600s). MUDD-136's reminder cron reuses this SHAPE — bounded
    exponential growth with jitter so a fleet's retries don't all land on a
    recovering Telegram at the same instant — but at a much smaller scale: it
    retries inline inside a fan-out over every due installment, not from a
    decoupled 5-minute sweep, so a 30s wait per retry would risk the whole job
    outrunning its own cron cadence under a real outage.
    """
    raw = min(base_seconds * (2 ** max(0, attempt - 1)), cap_seconds)
    return timedelta(seconds=raw * (0.5 + random.random() * 0.5))


def classify(exc: BaseException) -> str:
    """``permanent`` or ``transient``.

    Default is transient: an unrecognised condition is retried rather than
    abandoned, because giving up leaves a live-looking action forever.
    """
    from aiogram.exceptions import TelegramRetryAfter

    if isinstance(exc, TelegramRetryAfter):
        return "transient"
    text = str(exc).lower()
    if any(fragment in text for fragment in _PERMANENT_FRAGMENTS):
        return "permanent"
    return "transient"


def safe_error(exc: BaseException) -> str:
    """A short, bounded label. Never a message body, URL, token or phone."""
    return type(exc).__name__[:64]


async def record_notices(
    db: AsyncSession,
    *,
    payment_id: UUID,
    tenant_id: UUID,
    sent: list[tuple[int, int]],
    body: str | None = None,
) -> int:
    """Remember each message that carries a live review action.

    ``sent`` carries CANONICAL numeric ids from Telegram's own responses, so a
    target given as ``@channel`` is recorded like any other chat.
    """
    stored = 0
    for chat_id, message_id in sent:
        try:
            chat, message = int(chat_id), int(message_id)
        except (TypeError, ValueError):
            # Defensive: callers pass Telegram's own numeric ids. A malformed
            # pair is dropped rather than raised, because losing one retirement
            # reference must not fail the notification that was already sent.
            log.warning("payment_review.unusable_reference payment=%s", payment_id)
            continue
        notice = PaymentReviewNotice(
            payment_id=payment_id, tenant_id=tenant_id,
            chat_id=chat, message_id=message,
            state="pending", body=body,
        )
        try:
            async with db.begin_nested():
                db.add(notice)
                await db.flush()
            stored += 1
        except IntegrityError:
            if notice in db:
                db.expunge(notice)
    return stored


async def schedule_retirement(
    db: AsyncSession, *, payment_id: UUID, outcome: str, locale: str = "uz",
) -> int:
    """Mark this payment's live notices as due for retirement.

    Called in the SAME transaction that commits the decision, so the work
    becomes durable at the instant the decision does.
    """
    now = datetime.now(UTC)
    notices = (await db.execute(
        select(PaymentReviewNotice).where(
            PaymentReviewNotice.payment_id == payment_id,
            PaymentReviewNotice.state == "pending",
        )
    )).scalars().all()
    for notice in notices:
        notice.state = "retry"
        notice.outcome = outcome
        notice.locale = locale
        notice.next_attempt_at = now
    return len(notices)


async def claim_due(
    db: AsyncSession, *, limit: int = 25, now: datetime | None = None,
) -> list[PaymentReviewNotice]:
    """Take a lease on due notices, skipping any another worker holds.

    ``FOR UPDATE SKIP LOCKED`` is what makes concurrent workers safe: a locked
    row is passed over rather than waited for, so two workers cannot edit the
    same message and neither blocks.
    """
    moment = now or datetime.now(UTC)
    rows = (await db.execute(
        select(PaymentReviewNotice).where(
            (
                (PaymentReviewNotice.state == "retry")
                & (PaymentReviewNotice.next_attempt_at <= moment)
            ) | (
                # A lease whose holder died.
                (PaymentReviewNotice.state == "finalizing")
                & (PaymentReviewNotice.lease_expires_at <= moment)
            ),
        )
        .order_by(PaymentReviewNotice.next_attempt_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )).scalars().all()

    for notice in rows:
        notice.state = "finalizing"
        notice.lease_expires_at = moment + timedelta(seconds=LEASE_SECONDS)
    await db.flush()
    return list(rows)


def final_text(notice: PaymentReviewNotice) -> str:
    """The retired message: the original summary plus the outcome.

    Context is kept deliberately. Replacing the whole message with a bare status
    erases the customer, contract and amount a reader needs to know what was
    decided — and that context is our own rendered text, never read back from
    Telegram.
    """
    from app.core.documents import normalize_locale

    label = _FINAL_LABEL[normalize_locale(notice.locale)].get(
        notice.outcome or "", notice.outcome or "",
    )
    if notice.body:
        return f"{notice.body}\n\n{label}"
    return label


async def attempt_finalize(db: AsyncSession, notice: PaymentReviewNotice) -> bool:
    """One retirement attempt. Never raises.

    Returns True when the action is gone. On failure the notice is rescheduled,
    or abandoned once permanently impossible or out of budget — abandoned rows
    stay in the table, so the condition remains observable.
    """
    import asyncio

    from app.services.notifications import get_bot

    now = datetime.now(UTC)
    notice.attempt_count = (notice.attempt_count or 0) + 1
    notice.last_attempt_at = now
    try:
        async with asyncio.timeout(10):
            # `reply_markup=None` removes the keyboard: Telegram offers no way
            # to render an inline button as disabled.
            await get_bot().edit_message_text(
                chat_id=notice.chat_id,
                message_id=notice.message_id,
                text=final_text(notice),
                reply_markup=None,
            )
    except Exception as exc:
        notice.error = safe_error(exc)
        if classify(exc) == "permanent" or notice.attempt_count >= MAX_FINALIZE_ATTEMPTS:
            notice.state = "abandoned"
            notice.lease_expires_at = None
            log.warning(
                "payment_review.finalize_abandoned payment=%s chat=%s attempts=%s reason=%s",
                notice.payment_id, notice.chat_id, notice.attempt_count, notice.error,
            )
        else:
            notice.state = "retry"
            notice.next_attempt_at = now + _backoff(notice.attempt_count)
            notice.lease_expires_at = None
            log.info(
                "payment_review.finalize_retry payment=%s attempts=%s reason=%s",
                notice.payment_id, notice.attempt_count, notice.error,
            )
        return False

    notice.state = "finalized"
    notice.finalized_at = now
    notice.lease_expires_at = None
    notice.next_attempt_at = None
    notice.error = None
    return True


async def finalize_claimed(db: AsyncSession, notices: list[PaymentReviewNotice]) -> int:
    done = 0
    for notice in notices:
        if await attempt_finalize(db, notice):
            done += 1
    await db.flush()
    return done


async def finalize_due(db: AsyncSession, *, limit: int = 25) -> int:
    """Claim and retire whatever is due — the unit a worker runs."""
    claimed = await claim_due(db, limit=limit)
    if not claimed:
        return 0
    # (MUDD-63/M1) Commit the claim before the Telegram calls in
    # finalize_claimed. The lease (state="finalizing" + lease_expires_at) is
    # what actually protects a claimed notice from a second worker, per
    # claim_due's own reclaim condition above — the FOR UPDATE SKIP LOCKED row
    # lock only needs to live long enough to durably record that lease, not
    # for the rest of the retirement. Holding it across a slow/timed-out
    # Telegram edit would tie up a pool connection and this row's lock for the
    # duration of that call for no correctness benefit.
    await db.commit()
    return await finalize_claimed(db, claimed)


async def retire_now_bg(*, payment_id: UUID) -> None:
    """Best-effort immediate retirement, after the decision has committed.

    Losing this is harmless: the rows are already marked due, so the sweep picks
    them up. It exists only so the common case is fast.
    """
    # (Batch 5) Background delivery runs under the explicit WORKER authority:
    # it spans tenants and has no request context to inherit a scope from.
    from app.db import worker_session

    try:
        async with worker_session() as db:
            now = datetime.now(UTC)
            notices = (await db.execute(
                select(PaymentReviewNotice)
                .where(
                    PaymentReviewNotice.payment_id == payment_id,
                    PaymentReviewNotice.state == "retry",
                )
                .with_for_update(skip_locked=True)
            )).scalars().all()
            for notice in notices:
                notice.state = "finalizing"
                notice.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
            await db.flush()
            # (MUDD-63/M1) See finalize_due: commit the claim before the
            # Telegram calls so the row lock and pool connection aren't held
            # for the duration of those calls — the lease already claimed
            # above is what keeps this notice safe from a second worker.
            await db.commit()
            await finalize_claimed(db, list(notices))
            await db.commit()
    except Exception:
        log.warning("payment_review.retire_now_failed payment=%s", payment_id)


async def sweep_review_notices_job() -> None:
    """Scheduler entry point: retire everything due, including work lost to a
    restart and leases abandoned by a dead process."""
    from app.db import worker_session

    try:
        async with worker_session() as db:
            done = await finalize_due(db)
            await db.commit()
        if done:
            log.info("payment_review.sweep_finalized count=%s", done)
    except Exception:
        log.exception("payment_review.sweep_failed")
