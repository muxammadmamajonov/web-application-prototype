"""Telegram-group media storage — the low-level send/download/topic primitives (#814).

Tenant media (payment proofs + generic files) is sent as a **document** into a
single private FORUM supergroup, one **topic per tenant**, via the platform bot,
then downloaded back through the same bot for the in-app proxy. We persist only
references (``app/models/media.py``); the bytes live in Telegram.

Hard Telegram limits this module enforces / relies on:

* ``getFile`` downloads are capped at **20 MB** (a bot can *send* up to 50 MB but
  can never download a larger file) → ``telegram_media_max_bytes`` guards it.
* ``file_id`` is **bot-specific** — the download must use the SAME bot that sent
  the file. We use the platform bot for both, so the token is always known.
* the ``getFile`` ``file_path`` embeds the bot token and expires (~1 h); aiogram's
  ``download_file`` fetches it server-side — we never cache or expose it.
* forum topics need the bot to be an admin with ``can_manage_topics``.

Every call is gated by the shared ``telegram_breaker`` so a Telegram outage fails
fast (``TelegramMediaUnavailable``) instead of hanging the request. A per-file
problem raises ``TelegramMediaError``.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramRetryAfter,
)
from aiogram.types import BufferedInputFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

log = logging.getLogger("muddato.telegram_media")

# Telegram's "General" thread is message_thread_id 1 — it always exists and is
# never created. We reserve it for platform-owner (HQ) media; every tenant gets
# its own created topic.
GENERAL_TOPIC_ID = 1

_SEND_TIMEOUT = 20      # seconds — documents are larger than a text notification
_DOWNLOAD_TIMEOUT = 20
_MAX_RETRY_AFTER = 5    # cap a hostile flood-control value


class TelegramMediaError(RuntimeError):
    """A media send/download failed (per-file API error, or oversize)."""


class TelegramMediaUnavailable(TelegramMediaError):
    """Telegram is unreachable / the breaker is open — the caller should fall back
    to the durable copy (dual-write) or surface a retryable error."""


class TelegramTopicMissing(TelegramMediaError):
    """The target forum topic no longer exists (deleted/closed on Telegram). The
    caller should re-provision the tenant's topic (``ensure_tenant_topic(...,
    force_recreate=True)``) and retry the send once."""


def _is_missing_topic_error(exc: Exception) -> bool:
    """Whether a Telegram API error means the forum topic is gone. Telegram has no
    stable error codes for this — only the ``description`` text — so we match the
    known phrases (``message thread not found`` is what ``sendDocument`` returns for
    a deleted/closed topic)."""
    msg = str(exc).lower()
    return (
        "thread not found" in msg
        or "thread_not_found" in msg
        or "topic_deleted" in msg
        or "topic_closed" in msg
    )


@dataclass(frozen=True)
class TelegramRef:
    """The durable reference Telegram hands back for a stored document."""

    file_id: str
    file_unique_id: str
    message_id: int
    file_name: str | None
    mime_type: str | None
    file_size: int | None


def media_chat_id() -> int:
    cid = settings.telegram_media_chat_id
    if cid is None:
        raise TelegramMediaError("telegram_media_chat_id_unset")
    return int(cid)


def _platform_bot() -> Bot:
    # The single shared platform bot owns every media file_id (see module docstring).
    from app.services.notifications import get_bot

    return get_bot()


def _opaque_filename(filename: str) -> str:
    """Strip the extension Telegram would sniff, keeping the name for humans.

    Telegram classifies an upload by its FILENAME EXTENSION, not by the
    ``sendDocument`` method: a ``.webp`` is ingested as a **sticker**, so the
    response carries ``message.sticker`` and ``message.document`` is empty. The
    caller then sees ``no_document_in_response`` and the whole send fails.

    That is not an edge case here — ``sanitize_image`` re-encodes EVERY uploaded
    receipt to WebP (it strips EXIF and normalises the format), so with the
    original name every single payment proof failed the Telegram leg and fell back
    to durable storage. The feature was configured, enabled, and silently doing
    nothing but doubling writes. Verified against the live group: the same bytes
    sent as ``probe.webp`` came back ``sticker=True, document=False``, and as
    ``probe.bin`` came back ``document=True``.

    Appending ``.bin`` costs nothing: the real content type is persisted on the
    ``media_files`` row and the download proxy serves from that, never from the
    Telegram-side name. The original stem is retained so a human scrolling the
    group can still tell what a file is.
    """
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return f"{stem}.bin"


async def send_document_to_topic(
    data: bytes,
    *,
    filename: str,
    thread_id: int,
    caption: str | None = None,
    chat_id: int | None = None,
    bot: Bot | None = None,
) -> TelegramRef:
    """Send ``data`` as a document into ``thread_id`` of the media group and return
    its :class:`TelegramRef`. Raises :class:`TelegramMediaUnavailable` on an outage
    or :class:`TelegramMediaError` on a per-file failure (incl. exceeding the ceiling)."""
    from app.core.breaker import telegram_breaker

    # Enforce the getFile ceiling on SEND too: Telegram lets a bot *send* up to
    # 50 MB but only *download* ≤20 MB, so anything larger than
    # ``telegram_media_max_bytes`` would be stored yet never retrievable through the
    # proxy. Reject it here so the service upholds its own contract for any caller
    # (the upload endpoints cap smaller still, upstream).
    if len(data) > settings.telegram_media_max_bytes:
        raise TelegramMediaError(f"file_too_large_{len(data)}")

    if not telegram_breaker.allow():
        raise TelegramMediaUnavailable("telegram_circuit_open")
    bot = bot or _platform_bot()
    chat = chat_id if chat_id is not None else media_chat_id()

    async def _send():
        async with asyncio.timeout(_SEND_TIMEOUT):
            # A fresh InputFile per send — the stream is consumed on upload.
            return await bot.send_document(
                chat,
                document=BufferedInputFile(data, filename=_opaque_filename(filename)),
                # The General topic is addressed by OMITTING the thread id. Passing
                # its nominal id 1 is rejected with "message thread not found" —
                # confirmed against the live group — which would have failed every
                # platform/subscription receipt, the exact path that routes there.
                message_thread_id=(None if thread_id == GENERAL_TOPIC_ID else thread_id),
                caption=caption or None,
            )

    try:
        msg = await _send()
    except TelegramRetryAfter as e:  # flood control — honour retry_after once
        wait = min(int(getattr(e, "retry_after", 1) or 1), _MAX_RETRY_AFTER)
        log.warning("telegram_media.flood_wait retry_after=%s", wait)
        try:
            await asyncio.sleep(wait)
            msg = await _send()
        except (TimeoutError, TelegramAPIError) as e2:
            telegram_breaker.record_failure()
            raise TelegramMediaError(f"send_failed_after_flood: {type(e2).__name__}") from None
    except TimeoutError:
        telegram_breaker.record_failure()
        raise TelegramMediaUnavailable("send_timeout") from None
    except TelegramAPIError as e:
        # A per-file API error (e.g. a deleted topic) is not necessarily an outage —
        # don't trip the breaker. A gone topic gets its own type so the caller can
        # re-provision + retry; anything else is a generic per-file failure.
        if _is_missing_topic_error(e):
            raise TelegramTopicMissing(f"topic_missing: {e}") from None
        raise TelegramMediaError(f"send_failed: {e}") from None

    telegram_breaker.record_success()
    doc = msg.document
    if doc is None:
        raise TelegramMediaError("no_document_in_response")
    return TelegramRef(
        file_id=doc.file_id,
        file_unique_id=doc.file_unique_id,
        message_id=msg.message_id,
        file_name=doc.file_name,
        mime_type=doc.mime_type,
        file_size=doc.file_size,
    )


async def delete_document_from_topic(
    *, chat_id: int, message_id: int, bot: Bot | None = None,
) -> bool:
    """Delete one archived document message. ``True`` when it is gone.

    "Gone" deliberately includes *already* gone. Telegram answers a delete for a
    message that no longer exists (or is older than the delete window) with a
    Bad Request, and for a retention sweep that is success, not failure — the
    goal is "this message is not in the topic", and a second sweep must not keep
    retrying a message nobody can find. Anything else — an outage, flood
    control, a revoked bot — returns False so the caller keeps the row and tries
    again, rather than dropping the only record of what still needs cleaning up.
    """
    from app.core.breaker import telegram_breaker

    if not telegram_breaker.allow():
        return False
    bot = bot or _platform_bot()
    try:
        async with asyncio.timeout(_SEND_TIMEOUT):
            await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest as exc:
        # message to delete not found / message can't be deleted → nothing to do.
        log.info("telegram_media.delete_noop message_id=%s reason=%s",
                 message_id, type(exc).__name__)
        return True
    except Exception as exc:
        telegram_breaker.record_failure()
        log.warning("telegram_media.delete_failed message_id=%s reason=%s",
                    message_id, type(exc).__name__)
        return False
    telegram_breaker.record_success()
    return True


async def download_document(
    file_id: str, *, bot: Bot | None = None, max_bytes: int | None = None,
) -> bytes:
    """Download a document's bytes via the owning bot, enforcing the size ceiling.

    Raises :class:`TelegramMediaUnavailable` on an outage and
    :class:`TelegramMediaError` when the file exceeds the 20 MB ``getFile`` limit.
    The Telegram file URL/token is handled entirely inside aiogram — never returned."""
    from app.core.breaker import telegram_breaker

    if not telegram_breaker.allow():
        raise TelegramMediaUnavailable("telegram_circuit_open")
    bot = bot or _platform_bot()
    cap = max_bytes if max_bytes is not None else settings.telegram_media_max_bytes

    try:
        async with asyncio.timeout(_DOWNLOAD_TIMEOUT):
            f = await bot.get_file(file_id)
            if f.file_size is not None and cap and f.file_size > cap:
                raise TelegramMediaError(f"file_too_large_{f.file_size}")
            if not f.file_path:
                raise TelegramMediaError("no_file_path")
            buf = await bot.download_file(f.file_path)
    except TelegramMediaError:
        raise
    except TimeoutError:
        telegram_breaker.record_failure()
        raise TelegramMediaUnavailable("download_timeout") from None
    except TelegramAPIError as e:
        raise TelegramMediaUnavailable(f"download_failed: {e}") from None

    telegram_breaker.record_success()
    if buf is None:  # aiogram returns the stream only when no destination is given
        raise TelegramMediaError("download_returned_empty")
    raw = buf.read()
    if cap and len(raw) > cap:
        raise TelegramMediaError(f"file_too_large_{len(raw)}")
    return raw


async def create_topic(name: str, *, chat_id: int | None = None, bot: Bot | None = None) -> int:
    """Create a forum topic and return its ``message_thread_id``. Requires the bot
    to be an admin with ``can_manage_topics`` in the group."""
    from app.core.breaker import telegram_breaker

    if not telegram_breaker.allow():
        raise TelegramMediaUnavailable("telegram_circuit_open")
    bot = bot or _platform_bot()
    chat = chat_id if chat_id is not None else media_chat_id()
    try:
        async with asyncio.timeout(_SEND_TIMEOUT):
            topic = await bot.create_forum_topic(chat, name=name[:128])
    except TimeoutError:
        telegram_breaker.record_failure()
        raise TelegramMediaUnavailable("create_topic_timeout") from None
    except TelegramAPIError as e:
        raise TelegramMediaError(f"create_topic_failed: {e}") from None
    telegram_breaker.record_success()
    return int(topic.message_thread_id)


async def ensure_tenant_topic(
    db: AsyncSession, tenant_id: UUID | None, *, bot: Bot | None = None,
    force_recreate: bool = False,
) -> int:
    """Resolve the topic id for a tenant, creating it lazily on first use.

    Platform media (``tenant_id is None``) → :data:`GENERAL_TOPIC_ID`. Otherwise the
    topic is created once via :func:`create_topic` and persisted on
    ``Tenant.telegram_topic_id`` (idempotent — a stored id is returned without an
    API call). The caller's transaction commits the flush.

    Concurrency-safe: the tenant row is locked ``FOR UPDATE`` so two simultaneous
    first-uploads can't each create a topic (a read-read-create race would leave one
    orphaned topic with unreachable files). The loser blocks until the winner commits
    the new id, then returns it without a second API call.

    ``force_recreate`` ignores any stored id and provisions a fresh topic — used when
    a send revealed the topic was deleted on Telegram's side (``TelegramTopicMissing``).
    Platform General (``tenant_id is None``) can't be deleted, so it short-circuits."""
    if tenant_id is None:
        return GENERAL_TOPIC_ID

    from sqlalchemy import select

    from app.models import Tenant

    # FOR UPDATE serializes concurrent provisioners on this tenant row.
    tenant = (await db.execute(
        select(Tenant).where(Tenant.id == tenant_id).with_for_update()
    )).scalar_one_or_none()
    if tenant is None:
        return GENERAL_TOPIC_ID
    if tenant.telegram_topic_id and not force_recreate:
        return int(tenant.telegram_topic_id)

    label = tenant.name or tenant.slug or str(tenant.id)
    thread_id = await create_topic(f"{label} · {str(tenant.id)[:8]}", bot=bot)
    tenant.telegram_topic_id = thread_id
    await db.flush()
    return thread_id
