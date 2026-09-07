"""Sending a generated document to the person who asked for it.

INVARIANT: at most one delivery row per (person, document, content version),
arbitrated by a unique index — two concurrent requests both read "nothing sent
yet", so only the database can decide.

NOT GUARANTEED: exactly-once external delivery. Telegram has no shared
transaction, so a send that succeeds while the caller's commit fails is
re-sent later. See docs/audits for the trade-off.

INVARIANT: a `file_id` is bot-scoped, so `bot_owner` is part of every cache
key. It is a delivery cache, never an authorization.

INVARIANT: callers authorize first; nothing here decides who may have a
document.
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.documents import (
    document_filename,
    long_date,
    long_datetime,
    money_with_currency,
    normalize_locale,
    payment_method_label,
    payment_status_label,
)
from app.models import Customer, DocumentDelivery, Installment, Payment, Tenant, User

log = logging.getLogger("muddato.document_delivery")

#: Telegram refuses a document caption longer than this. Exceeding it fails the
#: WHOLE send, so a caption is truncated rather than allowed to lose the file.
MAX_CAPTION = 1024

#: How many documents one person may pull in a window. Generous for a human
#: tapping buttons, and a firm ceiling on a script: each request renders a PDF,
#: which is the expensive part.
RATE_LIMIT_MAX = 20
RATE_LIMIT_WINDOW_SECONDS = 300

#: How long a `sending` row is believed to be live work. Far longer than a slow
#: Telegram round trip, so an ordinary in-flight request is never taken over —
#: which is the only way a lease could itself cause a duplicate.
SENDING_LEASE_SECONDS = 900


class DeliveryState(StrEnum):
    """What the client should show. A code, never prose."""

    sent = "SENT"
    #: Already in their chat from an earlier request — same outcome, no second
    #: message. The client says "sent", because from the user's side it is.
    already_sent = "ALREADY_SENT"
    #: Another request for this exact document is in flight.
    in_progress = "IN_PROGRESS"
    #: The person has no Telegram identity, or never started the bot. Not an
    #: error they caused; the client falls back to a browser download.
    unavailable = "UNAVAILABLE"
    rate_limited = "RATE_LIMITED"
    failed = "FAILED"


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    state: DeliveryState

    @property
    def ok(self) -> bool:
        return self.state in {DeliveryState.sent, DeliveryState.already_sent}


def _clip(text: str) -> str:
    """Keep a caption inside Telegram's limit, cutting at a line boundary.

    Degrading gracefully matters more than completeness: a caption one byte too
    long does not truncate on Telegram's side, it rejects the message and the
    document never arrives.
    """
    if len(text) <= MAX_CAPTION:
        return text
    keep = text[: MAX_CAPTION - 1]
    cut = keep.rfind("\n")
    return (keep[:cut] if cut > MAX_CAPTION // 2 else keep) + "…"


def safe_display_name(name: str | None) -> str:
    """A customer's name as it may appear in a caption.

    Trimmed, collapsed, and bounded. A caption is a chat message that can be
    forwarded out of the shop, so this is the only identity field that goes in
    it — never a phone, a passport or a JSHSHIR.
    """
    cleaned = " ".join((name or "").split())
    if not cleaned:
        return "—"
    return cleaned if len(cleaned) <= 64 else cleaned[:63] + "…"


async def contract_caption(
    db: AsyncSession, installment_id: UUID, *, locale: str = "uz",
) -> str:
    loc = normalize_locale(locale)
    inst = (await db.execute(
        select(Installment).where(Installment.id == installment_id)
    )).scalar_one()
    tenant = (await db.execute(select(Tenant).where(Tenant.id == inst.tenant_id))).scalar_one()
    customer = (await db.execute(
        select(Customer).where(Customer.id == inst.customer_id)
    )).scalar_one_or_none()

    ref = inst.contract_number or str(inst.id)[:8].upper()
    lines = (
        [f"📄 Shartnoma {ref}", f"Do'kon: {tenant.name}",
         f"Mijoz: {safe_display_name(customer.full_name if customer else None)}",
         f"Summa: {money_with_currency(inst.total_amount, inst.currency, loc)}",
         f"Sana: {long_date(inst.start_date, loc)}"]
        if loc == "uz" else
        [f"📄 Договор {ref}", f"Магазин: {tenant.name}",
         f"Клиент: {safe_display_name(customer.full_name if customer else None)}",
         f"Сумма: {money_with_currency(inst.total_amount, inst.currency, loc)}",
         f"Дата: {long_date(inst.start_date, loc)}"]
    )
    return _clip("\n".join(lines))


async def receipt_caption(
    db: AsyncSession, payment_id: UUID, *, locale: str = "uz",
) -> str:
    loc = normalize_locale(locale)
    pay = (await db.execute(select(Payment).where(Payment.id == payment_id))).scalar_one()
    inst = (await db.execute(
        select(Installment).where(Installment.id == pay.installment_id)
    )).scalar_one()
    ref = inst.contract_number or str(inst.id)[:8].upper()
    when = long_datetime(pay.paid_at, loc) if pay.paid_at else long_date(datetime.now(UTC), loc)

    lines = (
        ["🧾 To'lov cheki", f"Shartnoma: {ref}",
         f"Summa: {money_with_currency(pay.amount, pay.currency, loc)}",
         f"To'lov turi: {payment_method_label(pay.method, loc)}",
         f"Holat: {payment_status_label(pay.status, loc)}",
         f"Sana: {when}"]
        if loc == "uz" else
        ["🧾 Чек об оплате", f"Договор: {ref}",
         f"Сумма: {money_with_currency(pay.amount, pay.currency, loc)}",
         f"Способ оплаты: {payment_method_label(pay.method, loc)}",
         f"Статус: {payment_status_label(pay.status, loc)}",
         f"Дата: {when}"]
    )
    return _clip("\n".join(lines))


async def document_reference(db: AsyncSession, kind: str, object_id: UUID) -> str | None:
    """The human identifier a document is named after — its contract number.

    A receipt is named for the contract it belongs to on purpose: that is the
    number the shop and the customer both quote, and it makes the two documents
    sort together in a downloads folder.
    """
    if kind == "contract":
        inst = (await db.execute(
            select(Installment).where(Installment.id == object_id)
        )).scalar_one_or_none()
        return inst.contract_number if inst else None
    if kind == "payment_receipt":
        row = (await db.execute(
            select(Installment.contract_number)
            .join(Payment, Payment.installment_id == Installment.id)
            .where(Payment.id == object_id)
        )).scalar_one_or_none()
        return row
    return None


async def build_caption(
    db: AsyncSession, kind: str, object_id: UUID, *, locale: str,
) -> str:
    if kind == "contract":
        return await contract_caption(db, object_id, locale=locale)
    if kind == "payment_receipt":
        return await receipt_caption(db, object_id, locale=locale)
    # An invoice is a shop-facing billing document; its own reference is enough
    # and there is no customer to name.
    ref = str(object_id)[:8].upper()
    return f"🧾 {'Hisob-faktura' if normalize_locale(locale) == 'uz' else 'Счёт'} {ref}"


def content_version(data: bytes) -> str:
    """A fingerprint of the rendered document.

    Part of the archive cache key so a regenerated contract that DIFFERS is
    never served from an earlier copy. Content-addressed rather than a version
    counter because nothing bumps a counter when a shop edits its template.
    """
    return hashlib.sha256(data).hexdigest()[:32]


async def _rate_limited(db: AsyncSession, user_id: UUID) -> bool:
    from datetime import timedelta

    from sqlalchemy import func

    since = datetime.now(UTC) - timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)
    count = (await db.execute(
        select(func.count()).select_from(DocumentDelivery).where(
            DocumentDelivery.user_id == user_id,
            DocumentDelivery.purpose == "user_delivery",
            DocumentDelivery.created_at >= since,
        )
    )).scalar_one()
    return bool(count >= RATE_LIMIT_MAX)


async def find_archived(
    db: AsyncSession, *, kind: str, object_id: UUID, version: str,
    bot_owner: str = "platform",
) -> DocumentDelivery | None:
    """A previously archived copy whose bytes are still the current bytes."""
    return (await db.execute(
        select(DocumentDelivery).where(
            DocumentDelivery.purpose == "archive",
            DocumentDelivery.kind == kind,
            DocumentDelivery.object_id == object_id,
            DocumentDelivery.document_version == version,
            DocumentDelivery.bot_owner == bot_owner,
            DocumentDelivery.status == "sent",
            DocumentDelivery.file_id.is_not(None),
        ).limit(1)
    )).scalar_one_or_none()


async def archive_document(
    db: AsyncSession,
    *,
    kind: str,
    object_id: UUID,
    tenant_id: UUID | None,
    data: bytes,
    filename: str,
    caption: str,
) -> DocumentDelivery | None:
    """Put a generated document in the tenant's archive topic and remember its id.

    Reuses the media group and per-tenant topic that already exist (#814) rather
    than standing up a second store. Best-effort by design: a failed archive
    must never fail the delivery the user asked for, because the archive is a
    cache and the document regenerates from the database regardless.

    Returns None when archival is not configured or did not succeed.
    """
    from app.config import settings
    from app.services.telegram_media import (
        TelegramMediaError,
        TelegramTopicMissing,
        ensure_tenant_topic,
        send_document_to_topic,
    )

    if not settings.telegram_media_enabled or settings.telegram_media_chat_id is None:
        return None

    version = content_version(data)
    existing = await find_archived(db, kind=kind, object_id=object_id, version=version)
    if existing is not None:
        return existing

    # Claim BEFORE uploading. Sending first let two concurrent archives both
    # upload the same bytes and leave one orphan message in the topic.
    row = DocumentDelivery(
        purpose="archive", kind=kind, object_id=object_id,
        document_version=version, tenant_id=tenant_id, status="sending",
        bot_owner="platform",
    )
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        if row in db:
            db.expunge(row)
        return await find_archived(db, kind=kind, object_id=object_id, version=version)

    try:
        thread_id = await ensure_tenant_topic(db, tenant_id)
        try:
            ref = await send_document_to_topic(
                data, filename=filename, thread_id=thread_id, caption=caption,
            )
        except TelegramTopicMissing:
            thread_id = await ensure_tenant_topic(db, tenant_id, force_recreate=True)
            ref = await send_document_to_topic(
                data, filename=filename, thread_id=thread_id, caption=caption,
            )
    except TelegramMediaError as exc:
        # The archive is a cache; a failed upload leaves a non-`sent` row that
        # the cache lookup ignores, and the next request may retry.
        row.status = "failed"
        row.error = type(exc).__name__
        await db.flush()
        log.warning("document_delivery.archive_failed kind=%s reason=%s",
                    kind, type(exc).__name__)
        return None

    row.status = "sent"
    row.chat_id = settings.telegram_media_chat_id
    row.message_thread_id = thread_id
    row.message_id = ref.message_id
    row.file_id = ref.file_id
    row.file_unique_id = ref.file_unique_id
    row.delivered_at = datetime.now(UTC)
    await db.flush()
    return row


async def deliver_to_user(
    db: AsyncSession,
    *,
    user: User,
    kind: str,
    object_id: UUID,
    tenant_id: UUID | None,
    render: Callable[[], Awaitable[bytes]],
    locale: str = "uz",
) -> DeliveryResult:
    """Send one document to one person, once per document VERSION.

    WHAT IS AND IS NOT GUARANTEED. The database gives idempotency: at most one
    delivery ROW per (person, document, version), arbitrated by a unique index
    because two concurrent requests both read "nothing sent yet".

    That is NOT exactly-once delivery. Telegram is an external side effect with
    no shared transaction, so one window is irreducible: the send succeeds and
    the caller's transaction then fails to commit, losing the row that
    remembers it. The next request sends again. The alternative — record first,
    commit, then send — trades that for silent non-delivery, which is worse for
    a document somebody asked for. See the batch report.

    The claim is inserted before the send, and rendering happens BEFORE the
    claim so the version is known: keying on the version is what lets a
    corrected document reach somebody who already has the superseded one.
    """
    # `telegram_id` is non-nullable — every account is Telegram-linked — so
    # this never fires today. The real "no Telegram identity" case is a
    # `TelegramForbiddenError` from `_send()` below: the person has a linked
    # id (from the Mini App's own initData) but has never opened a chat with
    # the bot, which Telegram enforces before a bot may message anyone.
    if not user.telegram_id:
        return DeliveryResult(DeliveryState.unavailable)

    if await _rate_limited(db, user.id):
        log.warning("document_delivery.rate_limited user=%s", user.id)
        return DeliveryResult(DeliveryState.rate_limited)

    # Rendered first so the claim can carry the content version. A repeat tap
    # therefore costs a render; the rate limiter is what bounds that.
    data = await render()
    version = content_version(data)

    existing = (await db.execute(
        select(DocumentDelivery).where(
            DocumentDelivery.purpose == "user_delivery",
            DocumentDelivery.user_id == user.id,
            DocumentDelivery.kind == kind,
            DocumentDelivery.object_id == object_id,
            DocumentDelivery.document_version == version,
            DocumentDelivery.status != "failed",
        ).limit(1)
    )).scalar_one_or_none()
    if existing is not None:
        if existing.status == "sent":
            return DeliveryResult(DeliveryState.already_sent)
        # A `sending` row that outlived its lease is abandoned work: the process
        # died between claiming and sending. The lease is far longer than a slow
        # Telegram round trip, so an ordinary in-flight request is never taken
        # over and cannot produce a duplicate.
        age = datetime.now(UTC) - (existing.created_at or datetime.now(UTC))
        if age.total_seconds() < SENDING_LEASE_SECONDS:
            return DeliveryResult(DeliveryState.in_progress)
        log.warning(
            "document_delivery.lease_expired user=%s kind=%s age=%ds",
            user.id, kind, int(age.total_seconds()),
        )
        existing.status = "failed"
        existing.error = "lease_expired"
        await db.flush()

    claim = DocumentDelivery(
        purpose="user_delivery", kind=kind, object_id=object_id,
        document_version=version, user_id=user.id, tenant_id=tenant_id,
        status="sending", bot_owner="platform",
    )
    try:
        async with db.begin_nested():
            db.add(claim)
            await db.flush()
    except IntegrityError:
        # A concurrent request won the claim. One delivery happens; this one
        # reports what the other is doing rather than racing it.
        if claim in db:
            db.expunge(claim)
        return DeliveryResult(DeliveryState.in_progress)

    caption = await build_caption(db, kind, object_id, locale=locale)
    reference = await document_reference(db, kind, object_id)
    filename = document_filename(kind, reference=reference, locale=locale)

    cached = await find_archived(db, kind=kind, object_id=object_id, version=version)
    send_state = await _send(
        telegram_id=user.telegram_id, data=data, filename=filename,
        caption=caption, file_id=cached.file_id if cached else None,
    )
    if send_state is not DeliveryState.sent:
        claim.status = "failed"
        claim.error = (
            "telegram_bot_not_started" if send_state is DeliveryState.unavailable
            else "telegram_send_failed"
        )
        await db.flush()
        return DeliveryResult(send_state)

    claim.status = "sent"
    claim.delivered_at = datetime.now(UTC)
    await db.flush()

    if cached is None:
        await archive_document(
            db, kind=kind, object_id=object_id, tenant_id=tenant_id,
            data=data, filename=filename, caption=caption,
        )
    return DeliveryResult(DeliveryState.sent)


async def _send(
    *, telegram_id: int, data: bytes, filename: str, caption: str,
    file_id: str | None = None,
) -> DeliveryState:
    """One document to one chat, preferring a cached ``file_id``.

    Resending by id costs one API call and no upload. A stale id — the archive
    message was deleted, or the cache crossed a bot boundary — falls back to
    uploading the bytes rather than failing the delivery.

    Returns `sent` / `unavailable` / `failed` rather than a bare bool: a
    ``TelegramForbiddenError`` ("bot can't initiate conversation with a user" /
    "bot was blocked by the user") means the person has never started a chat
    with the bot — the single most common cause a delivery does not arrive,
    and one the caller cannot retry its way out of. Collapsing it into the
    same `failed` as a genuine transient Telegram outage is what left the
    Mini App fallback with nothing better to say than an unactionable "open a
    browser" — this is the distinction that makes an honest, specific message
    possible instead.
    """
    import asyncio

    from aiogram.exceptions import TelegramForbiddenError
    from aiogram.types import BufferedInputFile

    from app.services.notifications import get_bot

    bot = get_bot()
    if file_id:
        try:
            async with asyncio.timeout(15):
                await bot.send_document(telegram_id, document=file_id, caption=caption or None)
            return DeliveryState.sent
        except TelegramForbiddenError as exc:
            log.info("document_delivery.file_id_reuse_forbidden chat=%s err=%s", telegram_id, exc)
            return DeliveryState.unavailable
        except Exception as exc:
            log.info("document_delivery.file_id_reuse_failed err=%s", exc)

    try:
        async with asyncio.timeout(20):
            await bot.send_document(
                telegram_id,
                document=BufferedInputFile(data, filename=filename),
                caption=caption or None,
            )
        return DeliveryState.sent
    except TelegramForbiddenError as exc:
        log.info("document_delivery.send_forbidden chat=%s err=%s", telegram_id, exc)
        return DeliveryState.unavailable
    except Exception as exc:
        log.warning("document_delivery.send_failed chat=%s err=%s", telegram_id, exc)
        return DeliveryState.failed
