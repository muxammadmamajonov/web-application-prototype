"""HTTP handlers that the Telegram bot uses: webhook + management endpoints.

Two flavours of webhook live here:

* Platform-bot ``POST /api/bot/webhook`` — receives updates for the shared
  ``@MuddatoBot``. The dispatcher and Bot instance are built once at import
  time (every tenant on a `shared` plan shares them).

* Per-tenant ``POST /api/bot/t/{tenant_id}/webhook`` — receives updates
  for a tenant's own bot (Pro+ plans, after they complete the
  Settings → Bot wizard). The Bot instance is built lazily from the
  token stored in ``tenant_settings.bot_token_ciphertext`` and cached
  per tenant so we don't pay the aiogram session-setup cost on every
  update.

  The same Router/Dispatcher (``app.bot.main.router``) handles updates
  for tenant bots too — `/start inv_<token>`, `/login`, `/help` and the
  fallback all just work. The tenant's bot effectively behaves like the
  platform bot, just under a different username.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import time
from collections.abc import Callable
from hashlib import sha256
from typing import Annotated
from uuid import UUID

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import MenuButtonWebApp, Update, WebAppInfo
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.main import build_bot, build_dispatcher, setup_commands
from app.config import settings
from app.core.authz import require_platform_admin
from app.core.dbauth import worker_authority
from app.core.security import current_user
from app.db import get_db
from app.models import TenantSettings, User

#: (Batch 5) Telegram delivers updates for arbitrary tenants to one authenticated webhook.
#: The DB authority is granted by the ROUTE, explicitly — never inferred
#: from a missing tenant header.
router = APIRouter(prefix="/bot", tags=["bot"], dependencies=[Depends(worker_authority)])
logger = logging.getLogger("muddato.bot.api")


_bot = build_bot()
_dp = build_dispatcher()

WEBHOOK_PATH = "/api/bot/webhook"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def _shared_secret() -> str:
    """Shared platform-bot webhook secret. Telegram's secret_token must be 1-256
    chars of [A-Za-z0-9_-] (#155); raw secret_key[:32] has no such guarantee, so
    derive it as an hmac-sha256 hexdigest (always hex-safe), mirroring
    _tenant_secret. NB: this rotates the secret — re-run POST /api/bot/set-webhook
    after deploy to re-register the platform webhook with the new token."""
    return hmac.new(
        settings.secret_key.encode(),
        b"bot-webhook:shared",
        sha256,
    ).hexdigest()[:32]


def _tenant_secret(tenant_id: UUID) -> str:
    """Deterministic per-tenant secret used as Telegram's webhook secret_token.

    Derived from the app SECRET_KEY so we don't have to store yet another
    secret per tenant. 32 hex chars fits comfortably under Telegram's
    1-256 char limit for ``secret_token``.
    """
    h = hmac.new(
        settings.secret_key.encode(),
        f"bot-webhook:{tenant_id}".encode(),
        sha256,
    ).hexdigest()
    return h[:32]


# (#154) Bounded, lazily-expiring caches for per-tenant aiogram Bot/Dispatcher
# instances. A plain dict grew unbounded and never reflected a deactivation done on
# another machine. Entries now expire (so a deactivated/re-tokened bot re-resolves
# from the DB within the TTL) and the dict can't exceed a max size.
_BOT_TTL = 600.0      # 10 min — also the cross-machine stale-after-deactivate window
_DISP_TTL = 3600.0    # dispatchers are stateless routers — just bound + periodic refresh
_CACHE_MAX = 512


class _TTLCache[V]:
    """Bounded TTL cache. Evicted/expired values are dropped, not eagerly closed —
    an aiogram Bot's HTTP session is reclaimed by GC; closing it here could race a
    concurrent request still holding the instance."""

    def __init__(self, *, ttl: float, maxsize: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl
        self._max = maxsize
        self._clock = clock
        self._d: dict[UUID, tuple[V, float]] = {}

    def get(self, key: UUID) -> V | None:
        item = self._d.get(key)
        if item is None:
            return None
        value, expires_at = item
        if expires_at <= self._clock():
            self._d.pop(key, None)
            return None
        return value

    def set(self, key: UUID, value: V) -> None:
        if key not in self._d and len(self._d) >= self._max:
            oldest = next(iter(self._d), None)  # insertion order ≈ oldest first
            if oldest is not None:
                self._d.pop(oldest, None)
        self._d[key] = (value, self._clock() + self._ttl)

    def pop(self, key: UUID) -> None:
        self._d.pop(key, None)

    def values(self) -> list[V]:
        return [value for value, _expires_at in self._d.values()]

    def clear(self) -> None:
        self._d.clear()


# A tenant that rotates/deactivates clears its DB row; the entry also expires after
# _BOT_TTL so other machines stop serving the stale Bot.
_tenant_bots: _TTLCache[Bot] = _TTLCache(ttl=_BOT_TTL, maxsize=_CACHE_MAX)


async def close_bots() -> None:
    """Release platform and cached tenant bot HTTP sessions at app shutdown."""
    bots = [_bot, *_tenant_bots.values()]
    _tenant_bots.clear()
    results = await asyncio.gather(
        *(bot.session.close() for bot in bots),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            logger.warning("bot.session_close_failed", exc_info=result)


def _build_tenant_bot(token: str) -> Bot:
    return Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def _get_tenant_bot(db: AsyncSession, tenant_id: UUID) -> Bot | None:
    cached = _tenant_bots.get(tenant_id)
    if cached is not None:
        return cached
    s = (await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
    )).scalar_one_or_none()
    if not s or not s.bot_token_ciphertext:
        return None
    bot = _build_tenant_bot(s.bot_token_ciphertext)
    _tenant_bots.set(tenant_id, bot)
    return bot


def _invalidate_tenant_bot(tenant_id: UUID) -> None:
    """Called by tenant_settings.deactivate to drop the cached Bot."""
    _tenant_bots.pop(tenant_id)


# ── webhook update dedup + timeout (#148) ──────────────────────────────────

_WEBHOOK_TIMEOUT = 30.0  # bound inline processing so a slow handler can't hold the request ~60s


async def _claim_update(scope: str, update_id: int | None) -> bool:
    """(#148) Dedup Telegram's at-least-once webhook retries: claim each update_id
    once per bot via Redis SET NX. Returns True to process, False if it's a
    duplicate. Fails OPEN (process) when Redis is unavailable — never drop updates."""
    if update_id is None:
        return True
    try:
        from app.core.redis import get_redis
        return bool(await get_redis().set(f"tg:upd:{scope}:{update_id}", "1", nx=True, ex=3600))
    except Exception:
        return True


async def _release_claim(scope: str, update_id: int | None) -> None:
    """(H5) Undo `_claim_update` after the handler crashes so Telegram's retry can
    actually reprocess the update. Without this, only `TimeoutError` was handled —
    any other exception left the Redis claim standing, so the retry hit
    `_claim_update` → False → `{"ok": True}` and the update was silently dropped
    forever. Best-effort: if Redis is unreachable the claim just expires on its
    own (`ex=3600`), same fail-open posture as `_claim_update`."""
    if update_id is None:
        return
    try:
        from app.core.redis import get_redis
        await get_redis().delete(f"tg:upd:{scope}:{update_id}")
    except Exception:
        logger.warning("tg.webhook.release_claim_failed scope=%s update_id=%s", scope, update_id)


# ── shared platform bot webhook ────────────────────────────────────────────

@router.post("/webhook", status_code=status.HTTP_200_OK)
async def telegram_webhook(
    request: Request,
    secret: str | None = Header(default=None, alias=SECRET_HEADER),
):
    """Receives Telegram updates for @MuddatoBot. Configure with /api/bot/set-webhook."""
    if secret is None or not hmac.compare_digest(secret.encode(), _shared_secret().encode()):  # (#132) constant-time
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad_secret")
    payload = await request.json()
    update = Update.model_validate(payload)
    if not await _claim_update("platform", update.update_id):
        return {"ok": True}  # (#148) duplicate Telegram retry — already processed
    try:
        await asyncio.wait_for(_dp.feed_update(_bot, update), _WEBHOOK_TIMEOUT)
    except TimeoutError:
        logger.warning("tg.webhook.timeout scope=platform update_id=%s", update.update_id)
    except Exception:
        # (H5) A handler crash must not leave this update permanently claimed —
        # release it so Telegram's retry is actually reprocessed instead of
        # silently swallowed as a "duplicate".
        logger.exception("tg.webhook.handler_error scope=platform update_id=%s", update.update_id)
        await _release_claim("platform", update.update_id)
        raise
    return {"ok": True}


@router.post("/set-webhook")
async def set_webhook(user: Annotated[User, Depends(current_user)]):
    """Idempotently register the webhook, slash commands, and chat menu button."""
    require_platform_admin(user)
    url = f"{settings.backend_url.rstrip('/')}{WEBHOOK_PATH}"
    await _bot.set_webhook(
        url=url,
        secret_token=_shared_secret(),
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query", "my_chat_member"],
    )
    await setup_commands(_bot)
    # Persistent menu button → opens the WebApp directly without typing /start.
    await _bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="Muddato'ni ochish",
            web_app=WebAppInfo(url=settings.frontend_url.rstrip("/")),
        ),
    )
    info = await _bot.get_webhook_info()
    return {
        "url": info.url,
        "pending": info.pending_update_count,
        "has_custom_certificate": info.has_custom_certificate,
        "menu_button_url": settings.frontend_url.rstrip("/"),
    }


@router.post("/delete-webhook")
async def delete_webhook(user: Annotated[User, Depends(current_user)]):
    require_platform_admin(user)
    await _bot.delete_webhook(drop_pending_updates=True)
    return {"ok": True}


@router.get("/info")
async def webhook_info(user: Annotated[User, Depends(current_user)]):
    require_platform_admin(user)
    # (QA L-2) Telegram is an upstream that can refuse us — a revoked or rotated
    # bot token answers Unauthorized, and that escaped as a bare 500
    # `internal_error`. This is the ONE screen an admin opens to find out why the
    # bot is misbehaving, so an opaque 500 hides the very answer it exists to
    # give. Report the upstream's verdict as a 502 with its message instead.
    try:
        info = await _bot.get_webhook_info()
        me = await _bot.get_me()
    except TelegramAPIError as e:
        logger.warning("bot.info_unavailable err=%s", e)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"telegram_unavailable — {type(e).__name__}",
        ) from e
    return {
        "username": me.username,
        "webhook_url": info.url,
        "pending": info.pending_update_count,
        "last_error_message": info.last_error_message,
    }


# ── per-tenant bot webhook (Pro+ plans) ───────────────────────────────────

# Per-tenant dispatchers exist so that aiogram's internal "current bot"
# context-var resolves correctly when handlers call ``bot.send_message``.
# Routers are shared (one set of handlers) but each dispatcher gets its
# own Bot bound through ``feed_update``, which is exactly what aiogram
# expects.
_tenant_dispatchers: _TTLCache[Dispatcher] = _TTLCache(ttl=_DISP_TTL, maxsize=_CACHE_MAX)


def _get_tenant_dispatcher(tenant_id: UUID) -> Dispatcher:
    dp = _tenant_dispatchers.get(tenant_id)
    if dp is None:
        dp = build_dispatcher()
        _tenant_dispatchers.set(tenant_id, dp)
    return dp


@router.post("/t/{tenant_id}/webhook", status_code=status.HTTP_200_OK)
async def tenant_telegram_webhook(
    tenant_id: UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    secret: str | None = Header(default=None, alias=SECRET_HEADER),
):
    """Receives Telegram updates for a tenant's own bot.

    Authenticity: Telegram echoes back the ``secret_token`` we registered
    in ``setWebhook`` as ``X-Telegram-Bot-Api-Secret-Token`` on every
    POST. We derive it deterministically from SECRET_KEY + tenant_id, so
    no extra storage is required. Anyone hitting this URL without the
    right secret gets 401.
    """
    if secret is None or not hmac.compare_digest(secret.encode(), _tenant_secret(tenant_id).encode()):  # (#132) constant-time
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad_secret")

    bot = await _get_tenant_bot(db, tenant_id)
    if bot is None:
        # Tenant deactivated their bot; tell Telegram everything's fine
        # so it stops retrying, then move on.
        logger.info("tenant_bot_not_configured tenant=%s — dropping update", tenant_id)
        return {"ok": True}

    payload = await request.json()
    update = Update.model_validate(payload)
    if not await _claim_update(str(tenant_id), update.update_id):
        return {"ok": True}  # (#148) duplicate Telegram retry — already processed
    dp = _get_tenant_dispatcher(tenant_id)
    try:
        await asyncio.wait_for(dp.feed_update(bot, update), _WEBHOOK_TIMEOUT)
    except TimeoutError:
        logger.warning("tg.webhook.timeout tenant=%s update_id=%s", tenant_id, update.update_id)
    except Exception:
        # (H5) Same claim-release as the platform webhook — a handler crash must
        # not permanently swallow this tenant's update.
        logger.exception("tg.webhook.handler_error tenant=%s update_id=%s", tenant_id, update.update_id)
        await _release_claim(str(tenant_id), update.update_id)
        raise
    return {"ok": True}
