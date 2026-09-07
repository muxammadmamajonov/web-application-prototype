"""Eskiz.uz SMS gateway client (Round 25).

Env-driven and fully optional: when ``ESKIZ_EMAIL`` / ``ESKIZ_PASSWORD`` are
unset, :func:`is_configured` returns False and callers skip SMS entirely
(reminders stay Telegram-only). This lets the code ship before sandbox creds
are wired into ``.env``.

Auth model
----------
Eskiz issues a bearer token from ``POST /auth/login`` (email + password) that
lasts ~30 days. We cache it in Redis (``eskiz:token``) with a conservative TTL
and transparently re-login on a 401. There's also a ``PATCH /auth/refresh``
endpoint but a fresh login is simpler and equally cheap at our volume.

Sending
-------
``POST /message/sms/send`` with ``{mobile_phone, message, from}``. Phone must
be a bare ``998XXXXXXXXX`` (12 digits, no ``+``). We validate/normalise UZ
numbers before sending and estimate the billable part count for budgeting.

[LEGAL_REVIEW] SMS marketing/transactional consent: outbound SMS to customers
must be limited to transactional payment reminders the customer has a
contractual relationship for. Bulk/marketing SMS needs separate opt-in and an
approved alpha-sender. Lawyer to confirm Uzbek "Reklama to'g'risida" + personal
data (O'zbekiston PDPL) requirements before enabling non-reminder SMS.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass

import httpx

from app.config import settings
from app.core.redis import get_redis

log = logging.getLogger("muddato.sms")

_TOKEN_KEY = "eskiz:token"
_TOKEN_TTL_SECONDS = 25 * 24 * 3600  # refresh well before Eskiz's ~30-day expiry

# GSM-7 single-message capacity is 160 chars (153 per part when concatenated);
# anything outside the basic set forces UCS-2 → 70 chars (67 per part).
_GSM_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)

_TAG_RE = re.compile(r"<[^>]+>")


def is_configured() -> bool:
    return bool(settings.eskiz_email and settings.eskiz_password)


def strip_html(text: str) -> str:
    """Telegram templates use <b>/<sub> tags; SMS is plain text."""
    return _TAG_RE.sub("", text or "").strip()


def normalize_uz_phone(phone: str | None) -> str | None:
    """Return a bare ``998XXXXXXXXX`` string, or None if not a valid UZ mobile.

    Accepts inputs like ``+998 90 123 45 67``, ``998901234567``, or the bare
    9-digit local part ``901234567``.
    """
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 9:               # local part, prepend country code
        digits = "998" + digits
    if len(digits) == 12 and digits.startswith("998"):
        return digits
    return None


def canonical_uz_phone(phone: str | None) -> str | None:
    """Return the single stored form of a UZ number — ``+998XXXXXXXXX``.

    This is what every phone column holds, and it is what makes "is this number
    already taken?" answerable at all. Storing what the operator typed meant
    ``+998 90 123 45 67``, ``998901234567`` and ``901234567`` were three
    different strings, so `uq_customer_tenant_phone` — a plain unique constraint
    on the raw column — let the same person be entered three times in one shop.
    Comparing cleverly on read cannot fix that; only one canonical form can.

    The leading ``+`` is cosmetic and deliberately outside the digits, so the
    functional index ``regexp_replace(phone, '[^0-9]', '', 'g')`` on `users`
    keeps computing exactly ``998XXXXXXXXX`` and needs no rebuild.
    """
    bare = normalize_uz_phone(phone)
    return None if bare is None else f"+{bare}"


def estimate_parts(text: str) -> int:
    if not text:
        return 1
    unicode_msg = any(ch not in _GSM_BASIC for ch in text)
    n = len(text)
    if unicode_msg:
        return 1 if n <= 70 else math.ceil(n / 67)
    return 1 if n <= 160 else math.ceil(n / 153)


def estimate_cost_uzs(text: str) -> int:
    return estimate_parts(text) * settings.sms_cost_per_part_uzs


@dataclass(slots=True)
class SmsResult:
    ok: bool
    parts: int
    cost_uzs: int
    provider_message_id: str | None = None
    error: str | None = None


async def _login() -> str | None:
    """Authenticate and cache a fresh token. Returns None on failure."""
    if not is_configured():
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            r = await http.post(
                f"{settings.eskiz_base_url.rstrip('/')}/auth/login",
                data={"email": settings.eskiz_email, "password": settings.eskiz_password},
            )
        if r.status_code != 200:
            log.warning("sms.login_failed status=%s body=%s", r.status_code, r.text[:200])
            return None
        token = (r.json().get("data") or {}).get("token")
        if not token:
            log.warning("sms.login_no_token body=%s", r.text[:200])
            return None
        await get_redis().set(_TOKEN_KEY, token, ex=_TOKEN_TTL_SECONDS)
        return token
    except Exception as e:
        log.warning("sms.login_error err=%s", e)
        return None


async def _get_token(*, force: bool = False) -> str | None:
    if not force:
        try:
            cached = await get_redis().get(_TOKEN_KEY)
            if cached:
                return cached
        except Exception as e:
            log.warning("sms.token_cache_read_error err=%s", e)
    return await _login()


async def send_sms(phone: str, text: str, *, sender: str | None = None) -> SmsResult:
    """Send a single SMS via Eskiz. Pure transport — budget/dedup live in the
    caller. Re-logs in once on a 401. Never raises; returns a structured result.
    """
    parts = estimate_parts(text)
    cost = parts * settings.sms_cost_per_part_uzs

    mobile = normalize_uz_phone(phone)
    if mobile is None:
        return SmsResult(ok=False, parts=parts, cost_uzs=0, error="invalid_uz_phone")
    if not is_configured():
        return SmsResult(ok=False, parts=parts, cost_uzs=0, error="sms_not_configured")

    body = strip_html(text)
    from_sender = sender or settings.eskiz_default_sender

    async def _attempt(token: str) -> httpx.Response:
        async with httpx.AsyncClient(timeout=15.0) as http:
            return await http.post(
                f"{settings.eskiz_base_url.rstrip('/')}/message/sms/send",
                headers={"Authorization": f"Bearer {token}"},
                data={"mobile_phone": mobile, "message": body, "from": from_sender},
            )

    # (#255) Shed load on a sustained Eskiz outage: when the breaker is OPEN,
    # fail fast instead of waiting on a 15s timeout per message (a reminder/
    # broadcast batch would otherwise stall the worker).
    from app.core.breaker import sms_breaker
    if not sms_breaker.allow():
        return SmsResult(ok=False, parts=parts, cost_uzs=0, error="sms_circuit_open")

    token = await _get_token()
    if not token:
        return SmsResult(ok=False, parts=parts, cost_uzs=0, error="auth_failed")

    try:
        r = await _attempt(token)
        if r.status_code == 401:    # token expired → re-login once
            token = await _get_token(force=True)
            if not token:
                return SmsResult(ok=False, parts=parts, cost_uzs=0, error="auth_failed")
            r = await _attempt(token)
    except Exception as e:
        # (#144) Don't log the full phone (PII) — last 4 digits are enough to correlate.
        log.warning("sms.send_error phone=***%s err=%s", (mobile or "")[-4:], e)
        sms_breaker.record_failure()   # transport error → counts toward the breaker
        return SmsResult(ok=False, parts=parts, cost_uzs=0, error=str(e)[:200])
    sms_breaker.record_success()       # reached Eskiz (even a 4xx body is "up")

    if r.status_code not in (200, 201):
        return SmsResult(ok=False, parts=parts, cost_uzs=0, error=f"http_{r.status_code}:{r.text[:160]}")

    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    msg_id = str(data.get("id") or data.get("message_id") or "") or None
    status = str(data.get("status") or "").lower()
    if status in ("error", "failed", "rejected"):
        return SmsResult(ok=False, parts=parts, cost_uzs=0, provider_message_id=msg_id,
                         error=f"provider:{status}")
    return SmsResult(ok=True, parts=parts, cost_uzs=cost, provider_message_id=msg_id)
