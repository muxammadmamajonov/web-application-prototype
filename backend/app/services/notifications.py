"""Telegram notification dispatcher.

All user-facing notifications in Muddato go through Telegram (no web bell,
no email). This service exposes a small async API that:

  - sends a message to a single user (by user_id or telegram_id),
  - sends a message to a tenant's configured target (owner DM, channel, or both),
  - renders a template string with placeholder substitution,
  - persists a Notification row for auditing,
  - handles transient Telegram errors with one retry.

It intentionally does NOT poll or schedule — that's the reminder
cron's job. This is the pure "send" primitive.
"""
from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.business_time import tashkent_day_start_utc
from app.models import Notification, TenantSettings, User

log = logging.getLogger("muddato.notifications")

# Telegram's documented bulk ceiling is ~30 messages/second to *different* users.
# The old 0.03s gap = ~33/s actually EXCEEDED it (intermittent 429s / ban risk on
# a large send). Pace at 20/s — comfortably under the ceiling with headroom for
# the per-send round-trip + flood-control jitter; correctness over raw speed for
# a money-platform bot. send_to_telegram still honours a per-message RetryAfter.
BROADCAST_MIN_INTERVAL = 0.05  # seconds between successful sends (~20 msg/s)

# Reuse a single Bot instance for all notification calls — the aiogram
# default is to use one HTTP session per bot, which is fine for our QPS.
_bot: Bot | None = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(
            token=settings.telegram_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    return _bot


async def close_bot() -> None:
    """Close and forget the shared notification bot's HTTP session.

    Web and worker processes both reuse this singleton. Explicit shutdown keeps
    deploy rollovers from leaking aiohttp connectors until process teardown.
    """
    global _bot
    bot, _bot = _bot, None
    if bot is not None:
        await bot.session.close()


def format_money(amount: Any) -> str:
    """A money amount as the UI writes it: `4 800 000`.

    Deliberately the SAME shape as ``frontend/src/lib/format.ts::money`` —
    space-grouped thousands, truncated to whole units. A reminder is the one
    place a customer sees a number the app also shows them on screen, and two
    rival formatters would make the same debt look like two different figures.

    Truncation (not rounding) matches the frontend's ``Math.trunc``: the
    authoritative number is the ledger's, and a reminder that rounded 4 800
    000.60 up to 4 800 001 would state a debt nobody owes.
    """
    if amount is None:
        return "0"
    try:
        whole = int(Decimal(str(amount)))
    except (InvalidOperation, ValueError, TypeError):
        # Unparseable input is echoed rather than swallowed: a reminder that
        # said "0" for a number we could not read would understate a debt.
        return str(amount)
    # A plain ASCII space, matching the frontend, which explicitly strips the
    # NBSP that `toLocaleString('ru-RU')` emits.
    return f"{whole:,}".replace(",", " ")


#: Placeholder names the Settings UI has always advertised to shop owners
#: (`reminders.templatesHint` in both locales) and that the pre-#MUDD-111 seed
#: wrote into every lazily-created tenant's templates. They are NOT dropped:
#: templates carrying them are sitting in tenant rows and in owners' heads.
#: They live here, in the one context builder, so preview and real delivery
#: cannot disagree about which names resolve — which is exactly how the defect
#: stayed invisible: the preview endpoint supplied them and the cron did not.
_LEGACY_ALIASES = {
    "contract": "contract_number",
    "days_left": "days_until_due",
}


def reminder_context(
    *,
    customer_name: str,
    currency: str,
    amount: Any,
    due_date: str,
    contract_number: str,
    remaining: Any,
    days_overdue: Any,
    days_until_due: Any,
    phone: str = "",
) -> dict[str, str]:
    """The ONE placeholder context a reminder template is rendered against.

    Every caller — the 09:00 cron, the Settings "test send" preview and the
    per-installment manual reminder — builds its variables here. Before this
    existed the preview built its own dict *with* the legacy aliases and the
    cron built one *without* them, so an owner previewing a template saw
    perfect copy while their customers received a literal ``{days_left}``.
    A shared builder makes that particular divergence unrepresentable.

    Money fields are formatted here rather than at the call sites, so no caller
    can pass a raw ``Decimal`` and ship ``4800000.00`` to a customer.
    """
    ctx: dict[str, str] = {
        "customer_name":   customer_name or "",
        # From the contract, so a shop trading in USD is not dunning its
        # customers in the wrong unit.
        "currency":        currency or "",
        "amount":          format_money(amount),
        "due_date":        due_date or "",
        "contract_number": contract_number or "",
        "remaining":       format_money(remaining),
        "days_overdue":    str(days_overdue),
        "days_until_due":  str(days_until_due),
        "phone":           phone or "",
    }
    for alias, canonical in _LEGACY_ALIASES.items():
        ctx[alias] = ctx[canonical]
    return ctx


def render(template: str, **vars: Any) -> str:
    """`{customer_name}` style placeholder substitution.

    A key that is NOT in ``vars`` is left in the text verbatim — ``{oops}``
    reaches the customer as ``{oops}``. That is the real behaviour and it is
    now what this docstring says; it previously promised "missing keys → blank",
    which is what made an unresolved placeholder look impossible rather than
    merely unnoticed. Build the vars with :func:`reminder_context` and the
    supported set is guaranteed rather than assumed.

    A key present with value ``None`` does become blank.
    """
    if not template:
        return ""
    out = template
    for k, v in vars.items():
        # (#101) Escape user-controlled values (names, contract fields) before
        # they land in a parse_mode=HTML message. A bare "<"/"&" in a name would
        # otherwise make Telegram reject the whole message (silent reminder loss),
        # besides being a minor injection vector. The template's own HTML tags are
        # untouched — only the substituted values are escaped.
        out = out.replace("{" + k + "}", "" if v is None else html.escape(str(v)))
    return out


def miniapp_deeplink(start_param: str) -> str:
    """A t.me link that LAUNCHES the Mini App at a deep-linked screen.

    Bot "Open" buttons use these so a reminder / status update opens straight
    INTO the Mini App (not a web page). The ``start_param`` grammar mirrors the
    frontend contract in ``frontend/src/lib/telegram/deepLinks.ts`` — e.g.
    ``pay_<installment_id>`` → the installment pay screen,
    ``inst_<id>`` → its detail. Allowed chars: A-Z a-z 0-9 _ - (no encoding
    needed for our id/UUID params).
    """
    bot = (settings.telegram_bot_username or "").lstrip("@")
    return f"https://t.me/{bot}?startapp={start_param}"


def installment_pay_deeplink(installment_id: Any) -> str:
    """Mini App "Open → pay" deep link for a payment reminder / due notice."""
    return miniapp_deeplink(f"pay_{installment_id}")


def tenant_installment_deeplink(installment_id: Any) -> str:
    """Mini App deep link to the SHOP-SIDE contract screen.

    Distinct from :func:`installment_pay_deeplink`, which resolves to the
    customer's own `/me/...` pay screen. An owner tapping that would land on a
    persona they are not, for a contract that is not theirs — so the owner's
    copy of an overdue alert needs its own intent.
    """
    return miniapp_deeplink(f"tinst_{installment_id}")


def billing_deeplink() -> str:
    """Mini App "Open → billing" deep link for a SHOP OWNER's money notice.

    Trial-expiry and dunning warnings tell an owner their shop is about to stop
    working; the action that fixes it is one screen away, and until now the
    message did not carry it. Mirrors the `billing` start param in
    ``frontend/src/lib/telegram/deepLinks.ts``.
    """
    return miniapp_deeplink("billing")


def _compose_broadcast_text(title: str, body: str) -> str:
    """Build a broadcast's HTML message body, escaping the tenant/HQ-authored
    copy (#749).

    ``title``/``body`` are plain-text fields (no formatting feature), so a bare
    "<" or "&" in them — e.g. "Skidka < 50% & podarok" — would otherwise make
    Telegram reject the whole parse_mode=HTML message and silently fail the
    broadcast for *every* recipient, besides being a minor injection vector. The
    wrapping ``<b> … </b>`` is template HTML and stays; only the values escape.
    """
    return f"<b>{html.escape(title)}</b>\n\n{html.escape(body)}"


#: Schemes Telegram accepts on an inline URL button. `tel:` is NOT one of them —
#: a shop's contact phone cannot become a button, and offering it anyway makes
#: Telegram reject the entire message.
_BUTTON_SCHEMES = ("https://", "http://", "tg://")


def _is_button_url(url: str | None) -> bool:
    return bool(url) and str(url).startswith(_BUTTON_SCHEMES)


@dataclass(frozen=True, slots=True)
class MiniAppButton:
    """A button that opens the Mini App at one screen, in whatever way the
    destination chat supports.

    Telegram offers ``web_app`` buttons ONLY in private chats. A shop whose
    notification target is a channel or a forum topic would have the whole
    message rejected for carrying one — so the same intent needs two encodings,
    and which one is used is decided per recipient rather than per message.

    ``deep_link`` is the group form: a ``t.me/<bot>?startapp=<intent>`` URL that
    launches the Main Mini App at the same screen. Both carry the SAME opaque
    intent, which is a pointer and not a credential — see
    ``app/core/review_intent.py``.
    """

    label: str
    mini_app_url: str      # https://<frontend>/... — private chats
    deep_link: str         # https://t.me/<bot>?startapp=... — groups and topics


def _is_private_chat(chat_id: int | str) -> bool:
    """Telegram's own convention: a user chat id is positive, a group/channel
    id is negative. A string target is an @channel — never private."""
    try:
        return int(chat_id) > 0
    except (TypeError, ValueError):
        return False


async def send_to_telegram(
    chat_id: int | str,
    text: str,
    *,
    photo_url: str | None = None,
    button: tuple[str, str] | None = None,          # (label, url)
    buttons: Sequence[tuple[str, str]] | None = None,  # a single row of (label, url)
    mini_app: MiniAppButton | None = None,
    #: When given, each successful send appends ``(chat_id, message_id)``. A
    #: message carrying a live action has to be findable again to retire it.
    #: Each successful send appends the CANONICAL ``(chat_id, message_id)``
    #: from Telegram's response — never the request target.
    track: list[tuple[int, int]] | None = None,
) -> bool:
    """Low-level send. Returns True on success, False on Telegram error.

    ``buttons`` renders one row — a purchase notice offers "open the app" and
    "contact the shop" side by side, and two stacked rows would push the message
    body off a phone screen. ``button`` stays as the one-button shorthand its
    existing callers use.

    A url that Telegram will not accept in an inline button (anything but
    http/https/tg:, notably ``tel:``) is DROPPED rather than sent: Telegram
    rejects the whole message for one bad button, which would silently lose the
    notification instead of just a link.
    """
    # (#255) Fast-fail when the Telegram API is in a sustained outage (breaker
    # OPEN), so a broadcast doesn't burn an 8s timeout per recipient. Only a
    # network TIMEOUT trips the breaker below — a per-recipient TelegramAPIError
    # (e.g. the user blocked the bot) is NOT a provider outage and must not.
    from app.core.breaker import telegram_breaker
    if not telegram_breaker.allow():
        log.warning("notifications.telegram_circuit_open chat=%s", chat_id)
        return False

    bot = get_bot()
    row = [*(buttons or []), *([button] if button else [])]
    usable = [(label, url) for label, url in row if _is_button_url(url)]
    keyboard: list[list[InlineKeyboardButton]] = []
    if mini_app is not None:
        # A `web_app` button opens the Mini App INSIDE Telegram with a signed
        # `initData` payload — which is what makes the destination screen
        # authenticate the person who tapped. Groups and topics do not support
        # it, so they get the deep link, which lands on the same screen through
        # the Main Mini App and authenticates identically once open.
        if _is_private_chat(chat_id):
            keyboard.append([InlineKeyboardButton(
                text=mini_app.label, web_app=WebAppInfo(url=mini_app.mini_app_url),
            )])
        elif _is_button_url(mini_app.deep_link):
            keyboard.append([InlineKeyboardButton(
                text=mini_app.label, url=mini_app.deep_link,
            )])
    if usable:
        keyboard.append([
            InlineKeyboardButton(text=label, url=url) for label, url in usable
        ])
    markup: InlineKeyboardMarkup | None = (
        InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None
    )

    # The CANONICAL destination, taken from Telegram's response rather than from
    # the request. A target given as "@channel" resolves to a numeric
    # `msg.chat.id`, and only that numeric id can address a later edit.
    sent_refs: list[tuple[int, int]] = []

    async def _do_send() -> None:
        # Hard timeout so a slow/unreachable Telegram API never hangs a request
        # (notifications are best-effort; the caller shouldn't wait forever).
        async with asyncio.timeout(8):
            if photo_url:
                msg = await bot.send_photo(chat_id, photo=photo_url, caption=text, reply_markup=markup)
            else:
                msg = await bot.send_message(chat_id, text=text, reply_markup=markup)
        # Retained so a caller that must later EDIT this message can find it.
        mid = getattr(msg, "message_id", None)
        canonical = getattr(getattr(msg, "chat", None), "id", None)
        if mid is not None and canonical is not None:
            sent_refs.append((int(canonical), int(mid)))

    try:
        await _do_send()
        telegram_breaker.record_success()
        if track is not None and sent_refs:
            track.append(sent_refs[-1])
        return True
    except TelegramRetryAfter as e:
        # (#75) Telegram flood control — honour retry_after once (capped so a
        # hostile value can't hang the caller), then make a single retry.
        wait = min(int(getattr(e, "retry_after", 1) or 1), 5)
        log.warning("notifications.flood_wait chat=%s retry_after=%s", chat_id, wait)
        try:
            await asyncio.sleep(wait)
            await _do_send()
            telegram_breaker.record_success()
            # Only the message that actually landed: a flood-wait retry may
            # append twice, and the last entry is the surviving one.
            if track is not None and sent_refs:
                track.append(sent_refs[-1])
            return True
        except TimeoutError as e2:
            log.warning("notifications.send_timeout_after_flood chat=%s err=%s", chat_id, e2)
            telegram_breaker.record_failure()
            return False
        except TelegramAPIError as e2:
            log.warning("notifications.send_failed_after_flood chat=%s err=%s", chat_id, e2)
            return False
    except TimeoutError as e:
        # Unreachable/slow API → a real outage signal; count it.
        log.warning("notifications.send_timeout chat=%s err=%s", chat_id, e)
        telegram_breaker.record_failure()
        return False
    except TelegramAPIError as e:
        # Per-recipient error (blocked bot, bad chat) — NOT a provider outage.
        log.warning("notifications.send_failed chat=%s err=%s", chat_id, e)
        return False


async def notify_user(
    db: AsyncSession,
    *,
    user_id: UUID,
    tenant_id: UUID | None,
    kind: str,
    title: str,
    body: str,
    link: str | None = None,
    photo_url: str | None = None,
    installment_id: UUID | None = None,
    persist: bool = True,
    retry_existing: bool = False,
) -> bool:
    """Send a notification to a user and (optionally) persist a Notification row.

    Used for per-customer payment reminders, payment-approved confirmations,
    broadcast deliveries, etc. The user must have an `telegram_id` for the
    actual Telegram send to happen; otherwise we still write the row so the
    user sees it when they next open the dashboard.
    """
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    sent = False
    if user and user.telegram_id:
        full = f"<b>{title}</b>\n\n{body}" if title else body
        sent = await send_to_telegram(
            user.telegram_id, full,
            photo_url=photo_url,
            button=("Ochish", link) if link else None,
        )
        from app.core.metrics import record_notification
        record_notification(kind=kind, result="sent" if sent else "failed")
    if persist:
        notification = None
        if retry_existing:
            # (MUDD-136) Must be the SAME calendar day the reminder cron dedups
            # on — `tashkent_day_start_utc()`, not a UTC-midnight boundary. The
            # cron's own dedup query (scheduler.py's `send_reminders_job`) already
            # made this switch; this window used to stay on UTC midnight, and in
            # the 19:00-24:00 UTC window (Tashkent already tomorrow, UTC calendar
            # day not yet rolled) that let a re-run of the SAME Tashkent day find
            # yesterday's row here even though the cron's own dedup correctly
            # decided "new day, send" — so every run in that window created a
            # fresh Telegram send while silently reusing yesterday's row (stale
            # title/body persisted, no new dedup marker ever written). Verified
            # against real Postgres: three job calls in that window before this
            # fix sent three times with `skipped_dup` never rising.
            day_start = tashkent_day_start_utc()
            notification = (await db.execute(
                select(Notification).where(
                    Notification.user_id == user_id,
                    Notification.tenant_id == tenant_id,
                    Notification.kind == kind,
                    Notification.installment_id == installment_id,
                    Notification.created_at >= day_start,
                ).order_by(Notification.created_at.desc()).limit(1)
            )).scalar_one_or_none()
        if notification is None:
            notification = Notification(
                user_id=user_id, tenant_id=tenant_id,
                kind=kind, title=title, body=body, link=link,
                installment_id=installment_id,
            )
            db.add(notification)
        notification.attempt_count = (notification.attempt_count or 0) + 1
        notification.last_attempt_at = datetime.now(UTC)
        if sent:
            notification.delivered_at = notification.last_attempt_at
        await db.commit()
    return sent


async def notify_tenant_target(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    kind: str,
    title: str,
    body: str,
    photo_url: str | None = None,
    mini_app: MiniAppButton | None = None,
    track: list[tuple[int, int]] | None = None,
) -> int:
    """Send to a tenant's configured target (owner DM / channel / both).

    Returns the number of successful sends. Use this for things the *tenant*
    needs to see (overdue alerts, big sale notifications, etc.) — not for
    things the *customer* needs to see.
    """
    from app.models import TenantUser
    from app.models.enums import TenantRole

    s = (await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
    )).scalar_one_or_none()
    target = (s.reminder_target if s else "owner") or "owner"
    channel_id = s.reminder_channel_id if s else None

    sends: list[int | str] = []
    if target in ("owner", "both"):
        # Send to every active owner of this tenant.
        rows = (await db.execute(
            select(User.telegram_id)
            .join(TenantUser, TenantUser.user_id == User.id)
            .where(
                TenantUser.tenant_id == tenant_id,
                TenantUser.role == TenantRole.owner,
                TenantUser.is_active.is_(True),
            )
        )).all()
        sends.extend([r[0] for r in rows if r[0]])
    if target in ("channel", "both") and channel_id:
        sends.append(channel_id)

    full = f"<b>{title}</b>\n\n{body}" if title else body
    ok = 0
    from app.core.metrics import record_notification
    for chat in sends:
        # Per recipient, not per message: the same notification goes to an
        # owner's DM (web_app button) and a channel (deep link) in one call.
        delivered = await send_to_telegram(
            chat, full, photo_url=photo_url, mini_app=mini_app, track=track,
        )
        record_notification(kind=kind, result="sent" if delivered else "failed")
        if delivered:
            ok += 1
    return ok


async def send_document_to_user(
    *,
    telegram_id: int | None,
    data: bytes,
    filename: str,
    caption: str = "",
) -> bool:
    """Send one generated file to the chat of the person who asked for it (#10).

    The sibling above delivers to a tenant's OWNERS, which is right for a report
    the shop asked for. This one delivers to the requesting user, which is what
    a contract or receipt needs: a customer tapping "download" must receive
    their own document, not have it posted to the shop.

    Returns False rather than raising when the user has no Telegram id or has
    never started the bot — the caller turns that into "open it in the browser
    instead", which is a working outcome, not an error.
    """
    from aiogram.types import BufferedInputFile

    if not telegram_id:
        return False
    try:
        bot = get_bot()
        async with asyncio.timeout(15):
            await bot.send_document(
                telegram_id, document=BufferedInputFile(data, filename=filename),
                caption=caption or None,
            )
        return True
    except Exception as e:
        log.warning("notifications.user_document_failed chat=%s err=%s", telegram_id, e)
        return False


async def send_tenant_document(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    data: bytes,
    filename: str,
    caption: str = "",
) -> int:
    """(#206) Send a report file to the tenant's active owners on Telegram.

    Returns the number of successful sends (0 if no owner has started the bot).
    Used so the owner can tap CSV/XLSX/PDF in the dashboard and receive the file
    in their Telegram chat instead of a browser download.
    """
    from aiogram.types import BufferedInputFile

    from app.models import TenantUser
    from app.models.enums import TenantRole

    rows = (await db.execute(
        select(User.telegram_id)
        .join(TenantUser, TenantUser.user_id == User.id)
        .where(
            TenantUser.tenant_id == tenant_id,
            TenantUser.role == TenantRole.owner,
            TenantUser.is_active.is_(True),
        )
    )).all()
    chats = [r[0] for r in rows if r[0]]
    if not chats:
        return 0

    bot = get_bot()
    ok = 0
    for chat in chats:
        try:
            async with asyncio.timeout(15):
                # Fresh InputFile per send — the stream is consumed on upload.
                await bot.send_document(
                    chat, document=BufferedInputFile(data, filename=filename),
                    caption=caption or None,
                )
            ok += 1
        except (TimeoutError, TelegramAPIError) as e:
            log.warning("notifications.send_document_failed chat=%s err=%s", chat, e)
    return ok


async def notify_payment_review_bg(
    *, tenant_id: UUID, payment_id: UUID, title: str, body: str,
    mini_app: MiniAppButton,
) -> None:
    """Send the receipt-review notice and REMEMBER each message it produced.

    A message carrying a live action is not finished being managed when it is
    sent: it has to be findable again so the action can be retired once the
    payment is decided.
    """
    # (Batch 5) Background delivery runs under the explicit WORKER authority:
    # it spans tenants and has no request context to inherit a scope from.
    from app.db import worker_session
    from app.services.payment_review_notify import record_notices

    try:
        async with worker_session() as db:
            # CANONICAL ids only: `track` appends what Telegram's response
            # carried, so a target given as `@channel` still lands here numeric.
            sent: list[tuple[int, int]] = []
            await notify_tenant_target(
                db, tenant_id=tenant_id, kind="receipt.uploaded",
                title=title, body=body, mini_app=mini_app, track=sent,
            )
            await record_notices(
                db, payment_id=payment_id, tenant_id=tenant_id, sent=sent,
                # The safe summary we rendered, kept so a retirement can append
                # the outcome instead of erasing the context.
                body=f"<b>{title}</b>\n\n{body}" if title else body,
            )
            await db.commit()
    except Exception:
        log.exception("notifications.review_bg_failed payment=%s", payment_id)


async def notify_tenant_target_bg(
    *, tenant_id: UUID, kind: str, title: str, body: str,
    photo_url: str | None = None, mini_app: MiniAppButton | None = None,
) -> None:
    """Background-safe variant: opens its own DB session so it can run *after*
    the HTTP response is sent (FastAPI BackgroundTasks). Keeps Telegram latency
    off the request's critical path — e.g. a customer uploading a cash receipt
    gets an instant response instead of waiting on the owner's Telegram send."""
    from app.db import worker_session
    try:
        async with worker_session() as db:
            await notify_tenant_target(
                db, tenant_id=tenant_id, kind=kind, title=title, body=body,
                photo_url=photo_url, mini_app=mini_app,
            )
    except Exception:  # background best-effort; never crash the worker
        log.exception("notifications.bg_failed tenant=%s kind=%s", tenant_id, kind)


# (#H1) Commit boundary for broadcast delivery: after every N recipients, not
# once at the end. The `app` machine that runs this background task has
# `kill_timeout = "10s"` and `auto_stop_machines = "suspend"` (ops/fly.toml) —
# a deploy or scale-to-zero can cut it off mid-send. Batching bounds a crash's
# loss to one in-flight batch instead of the whole broadcast, and every
# committed batch is what makes a resumed run's anti-join (below) work.
_BROADCAST_BATCH = 200


async def deliver_broadcast_bg(*, broadcast_id: UUID, tenant_id: UUID) -> None:
    """Deliver a broadcast off the request path (#75).

    Sends + records recipients in keyset batches of ``_BROADCAST_BATCH``,
    committing after every batch (#H1). Previously the whole audience was
    loaded in one query and every ``BroadcastRecipient`` row stayed
    uncommitted until a single ``db.commit()`` at the very end — a kill
    mid-send lost every send that batch had made, committed or not, leaving no
    record of who was actually messaged. Batched commits bound that loss, and
    re-invoking this function for the same ``broadcast_id`` (e.g. an operator
    retry after an interruption) is safe: the customer query anti-joins
    against recipients already recorded for this broadcast, so a resumed run
    never re-sends to someone already on file.
    """
    from datetime import UTC, datetime

    from app.db import worker_session
    from app.models import Broadcast, BroadcastRecipient, Customer
    from app.models.enums import BroadcastStatus, BroadcastTarget, CustomerStatus

    try:
        async with worker_session() as db:
            b = (await db.execute(
                select(Broadcast).where(Broadcast.id == broadcast_id)
            )).scalar_one_or_none()
            if b is None:
                return

            base = select(Customer).where(Customer.tenant_id == tenant_id)
            if b.target == BroadcastTarget.all:
                base = base.where(Customer.status != CustomerStatus.blocked)
            else:
                base = base.where(Customer.status == CustomerStatus.active)
            # (#H1) Skip customers already recorded for this broadcast so a
            # resumed/retried run never double-sends.
            already_sent = select(BroadcastRecipient.customer_id).where(
                BroadcastRecipient.broadcast_id == b.id,
                BroadcastRecipient.customer_id.is_not(None),
            )
            base = base.where(Customer.id.notin_(already_sent))

            button = (
                (b.button_label, b.button_url)
                if b.button_label and b.button_url else None
            )
            text = _compose_broadcast_text(b.title, b.body_uz)

            last_id = None
            while True:
                q = base
                if last_id is not None:
                    q = q.where(Customer.id > last_id)
                batch = (await db.execute(
                    q.order_by(Customer.id.asc()).limit(_BROADCAST_BATCH)
                )).scalars().all()
                if not batch:
                    break
                for c in batch:
                    ok = False
                    if c.telegram_id:
                        ok = await send_to_telegram(
                            c.telegram_id, text, photo_url=b.media_url, button=button,
                        )
                    db.add(BroadcastRecipient(
                        broadcast_id=b.id, customer_id=c.id, telegram_id=c.telegram_id,
                        status="sent" if ok else "failed",
                        delivered_at=datetime.now(UTC) if ok else None,
                        error=None if ok else ("no_telegram" if not c.telegram_id else "send_failed"),
                    ))
                    if ok:
                        await asyncio.sleep(BROADCAST_MIN_INTERVAL)  # stay under Telegram's bulk cap
                # (#H1) Durability boundary — a kill after this line keeps
                # every send up to here on record.
                await db.commit()
                last_id = batch[-1].id
                if len(batch) < _BROADCAST_BATCH:
                    break

            # Recomputed from the recipient rows (the source of truth) rather
            # than a running total, so a resumed run's counts are correct even
            # though this invocation only sent a subset of the audience.
            counts = dict((await db.execute(
                select(BroadcastRecipient.status, func.count())
                .where(BroadcastRecipient.broadcast_id == b.id)
                .group_by(BroadcastRecipient.status)
            )).all())
            sent = counts.get("sent", 0)
            failed = counts.get("failed", 0)
            b.sent_count = sent
            b.failed_count = failed
            b.finished_at = datetime.now(UTC)
            b.status = BroadcastStatus.sent if (sent > 0 or failed == 0) else BroadcastStatus.failed
            await db.commit()
    except Exception:  # background best-effort; never crash the worker
        log.exception("notifications.broadcast_bg_failed broadcast=%s", broadcast_id)


async def deliver_platform_broadcast_bg(*, broadcast_id: UUID) -> None:
    """Deliver a platform-wide (Muddato HQ) broadcast to a chosen user SEGMENT.

    The segment (clients / demo-shop owners / lapsed trials / …) is stored on
    ``broadcast.target_filter["segment"]``; recipients are resolved by
    ``user_segments`` and deduped by Telegram chat. Each recipient gets the body
    in their own locale (``body_ru`` for ru users when present, else ``body_uz``).
    Best-effort, off the request path. Defaults to "owners" for legacy rows.

    Sent + recorded in batches of ``_BROADCAST_BATCH`` with a commit after
    every batch, and recipients already recorded for this broadcast are
    skipped — same crash-durability and resume-safety as
    ``deliver_broadcast_bg`` (#H1); see that function's docstring.
    """
    from datetime import UTC, datetime

    from app.db import worker_session
    from app.models import Broadcast, BroadcastRecipient
    from app.models.enums import BroadcastStatus
    from app.services.user_segments import VALID_SEGMENTS, recipients_with_locale

    try:
        async with worker_session() as db:
            b = (await db.execute(
                select(Broadcast).where(Broadcast.id == broadcast_id)
            )).scalar_one_or_none()
            if b is None:
                return
            tf = b.target_filter if isinstance(b.target_filter, dict) else {}
            segment = tf.get("segment") or "owners"
            if segment not in VALID_SEGMENTS:
                segment = "owners"
            recipients = await recipients_with_locale(db, segment)

            # (#H1) Resume support: drop anyone already recorded for this
            # broadcast so a retried/resumed run never double-sends.
            already_sent = set((await db.execute(
                select(BroadcastRecipient.telegram_id).where(
                    BroadcastRecipient.broadcast_id == b.id,
                    BroadcastRecipient.telegram_id.is_not(None),
                )
            )).scalars().all())
            pending = [(tg, locale) for tg, locale in recipients if tg not in already_sent]

            button = (
                (b.button_label, b.button_url)
                if b.button_label and b.button_url else None
            )
            for start in range(0, len(pending), _BROADCAST_BATCH):
                for tg, locale in pending[start:start + _BROADCAST_BATCH]:
                    body = b.body_ru if (locale == "ru" and b.body_ru) else b.body_uz
                    text = _compose_broadcast_text(b.title, body)
                    ok = await send_to_telegram(tg, text, photo_url=b.media_url, button=button)
                    db.add(BroadcastRecipient(
                        broadcast_id=b.id, customer_id=None, telegram_id=tg,
                        status="sent" if ok else "failed",
                        delivered_at=datetime.now(UTC) if ok else None,
                        error=None if ok else "send_failed",
                    ))
                    if ok:
                        await asyncio.sleep(BROADCAST_MIN_INTERVAL)  # under Telegram's bulk cap
                # (#H1) Durability boundary — see deliver_broadcast_bg.
                await db.commit()

            counts = dict((await db.execute(
                select(BroadcastRecipient.status, func.count())
                .where(BroadcastRecipient.broadcast_id == b.id)
                .group_by(BroadcastRecipient.status)
            )).all())
            sent = counts.get("sent", 0)
            failed = counts.get("failed", 0)
            b.sent_count = sent
            b.failed_count = failed
            b.finished_at = datetime.now(UTC)
            b.status = BroadcastStatus.sent if (sent > 0 or failed == 0) else BroadcastStatus.failed
            await db.commit()
    except Exception:  # background best-effort; never crash the worker
        log.exception("notifications.platform_broadcast_bg_failed broadcast=%s", broadcast_id)
