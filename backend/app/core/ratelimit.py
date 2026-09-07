"""Lightweight Redis fixed-window rate limiting.

Used as a FastAPI dependency on abuse-prone endpoints (login/signup, payment
initiation, public contact/demo). Keyed by client IP for unauthenticated routes
and by user id where a bearer token is present. Fails **open**: if Redis is
unavailable we allow the request rather than locking everyone out — a limiter
outage must not become an availability outage.
"""
from __future__ import annotations

import hmac
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Depends, HTTPException, Request, status

from app.config import settings
from app.core.redis import get_redis

log = logging.getLogger("muddato.ratelimit")

# (#258 / F-27) Atomic fixed-window increment. ``INCR`` then a *separate*
# ``EXPIRE`` is two round-trips: if the process dies between them the key is left
# with no TTL and becomes a permanent counter that locks the identity out. This
# Lua script runs both server-side in one round-trip — the EXPIRE is set exactly
# on the first hit of a window — so the no-TTL window is eliminated entirely.
# Returns the post-increment count, which the caller compares against the limit
# (identical semantics to the old ``await r.incr(key)`` it replaces).
_INCR_EXPIRE_LUA = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


async def _incr_window(r: Any, key: str, window_seconds: int) -> int:
    """Atomically increment ``key`` and set its TTL on the first hit.

    Uses a single ``EVAL`` round-trip against Redis (see ``_INCR_EXPIRE_LUA``).
    Falls back to the legacy ``incr`` + ``expire`` pair only when the backend
    object exposes no ``eval`` (e.g. a minimal stub) — real ``redis.asyncio``
    clients always provide it, so production always takes the atomic path. The
    fallback is a capability shim, never an error-swallow: a genuinely
    unreachable Redis still raises and is handled by the caller's fail policy.
    """
    eval_ = getattr(r, "eval", None)
    if eval_ is not None:
        return int(await eval_(_INCR_EXPIRE_LUA, 1, key, window_seconds))
    count = int(await r.incr(key))
    if count == 1:
        await r.expire(key, window_seconds)
    return count


def _client_ip(request: Request) -> str:
    # (#258 / F-08) Resolve the *real end-user* IP from a proxy-trusted source,
    # never a raw client-controllable one. Topology: browser → Cloudflare edge →
    # CF Pages /api proxy (a Worker) → Fly. From Fly's view ``Fly-Client-IP`` is
    # Cloudflare's *shared egress* (identical for every user), so keying on it
    # collapses all users into ONE rate-limit bucket and login globally 429s.
    #
    # The real user IP is known only at the CF edge (CF-Connecting-IP). Our proxy
    # forwards it as ``X-Real-Client-IP`` together with a shared secret
    # (``X-Proxy-Secret``); we trust that IP ONLY when the secret matches, so a
    # request reaching Fly directly (bypassing the proxy) cannot forge it — this
    # keeps the F-08 anti-spoofing guarantee while restoring per-user keying.
    secret = settings.proxy_shared_secret
    if secret:
        provided = request.headers.get("x-proxy-secret", "")
        if provided and hmac.compare_digest(provided, secret):
            real = request.headers.get("x-real-client-ip", "").strip()
            if real:
                return real
    # No verified proxy header → fall back to the least-spoofable source available.
    # For a direct-to-Fly request Fly-Client-IP is the caller's real socket IP;
    # behind the proxy it degrades to CF's shared egress (the verified path above is
    # the production norm). X-Forwarded-For (last appended hop) and the socket peer
    # are final fallbacks for non-CF / local topologies.
    fly = request.headers.get("fly-client-ip")
    if fly:
        return fly.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _identity(request: Request) -> str:
    """Prefer the authenticated user id (stable, proxy-independent); fall back
    to client IP for anonymous callers."""
    authz = request.headers.get("authorization", "")
    if authz.lower().startswith("bearer "):
        try:
            from app.core.security import decode_token
            sub = decode_token(authz.split(" ", 1)[1]).get("sub")
            if sub:
                return f"u:{sub}"
        except Exception as exc:
            # An expired/forged/garbage bearer just means "bucket this caller by IP".
            # Deliberately NOT narrowed to HTTPException (what decode_token raises for
            # every InvalidTokenError): a fault in key resolution would then escape and 500 every
            # rate-limited endpoint, changing behaviour. Deliberately DEBUG and
            # exception-class-only: the header is attacker-controlled, so a warning here
            # is a remotely-triggerable log flood, and the token must never be echoed.
            log.debug("ratelimit.identity.token_undecodable err=%s", type(exc).__name__)
    return f"ip:{_client_ip(request)}"


def rate_limit(
    bucket: str, *, limit: int, window_seconds: int, fail_closed: bool = False,
) -> Callable[[Request], Coroutine[Any, Any, None]]:
    """Return a dependency enforcing ``limit`` requests per ``window_seconds``
    per (bucket, identity).

    ``fail_closed`` controls what happens when the limiter backend (Redis) is
    unreachable:

    * ``False`` (default) — **fail open**: allow the request. Correct for paths
      where availability beats enforcement (payment initiation, provider
      webhooks, the public contact form) — a Redis blip must not block money
      flow or a real settlement.
    * ``True`` — **fail closed**: refuse with 503. Correct for abuse-sensitive
      auth paths (login, signup) where silently dropping brute-force protection
      during a Redis outage is the more dangerous failure. Either way the
      degraded path is now **observable** (metric + warning log), never silent.
    """
    async def dependency(request: Request) -> None:
        key = f"rl:{bucket}:{_identity(request)}"
        try:
            r = get_redis()
            count = await _incr_window(r, key, window_seconds)
        except HTTPException:
            raise
        except Exception:
            # The limiter backend is down. Make it loud (was a silent allow),
            # then decide per the bucket's policy.
            decision = "deny" if fail_closed else "allow"
            try:
                from app.core.metrics import record_ratelimit_degraded
                record_ratelimit_degraded(bucket, decision)
            except Exception:
                # Must not swallow the ratelimit.redis_unavailable warning below —
                # that warning, not this counter, is what makes the degraded mode
                # observable. Debug keeps a metrics outage from doubling the noise.
                log.debug(
                    "ratelimit.metrics.degraded_record_failed bucket=%s decision=%s",
                    bucket, decision, exc_info=True,
                )
            log.warning(
                "ratelimit.redis_unavailable bucket=%s fail_closed=%s decision=%s",
                bucket, fail_closed, decision,
            )
            if fail_closed:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="rate_limiter_unavailable",
                    headers={"Retry-After": str(min(window_seconds, 30))},
                ) from None
            return
        if count > limit:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate_limited",
                headers={"Retry-After": str(window_seconds)},
            )
    # Introspectable so a route-to-limiter matrix test can assert, off the live app,
    # that a named expensive route still carries its bucket after any refactor.
    dependency._ratelimit_bucket = bucket            # type: ignore[attr-defined]
    dependency._ratelimit_fail_closed = fail_closed  # type: ignore[attr-defined]
    dependency._ratelimit_limit = limit              # type: ignore[attr-defined]
    dependency._ratelimit_window = window_seconds    # type: ignore[attr-defined]
    return dependency


# Pre-built dependencies for common buckets.
# Auth + signup fail CLOSED: dropping brute-force / account-creation-abuse
# protection during a Redis outage is more dangerous than a brief, alerted
# auth-unavailability. Payment-init, public form, and webhooks fail OPEN so a
# Redis blip never blocks money flow or a provider settlement.
def login_limit() -> Any:
    return Depends(rate_limit("auth", limit=30, window_seconds=60, fail_closed=True))


def signup_limit() -> Any:
    return Depends(rate_limit("signup", limit=10, window_seconds=3600, fail_closed=True))


def provision_trial_limit() -> Any:
    """Throttle authenticated trial provisioning (#756).

    Mirrors ``signup_limit``'s account-creation cadence (10/hour) but fails
    **OPEN**: unlike anonymous signup/login, this route already sits behind
    ``current_user`` — an attacker can't probe it without a valid bearer token,
    so the brute-force concern that makes signup fail-closed doesn't apply, and a
    Redis blip must not block legitimate trial onboarding (a conversion hit).
    Keyed per user id (``_identity`` reads the bearer ``sub``), so one tenant's
    bursts can't starve another's.
    """
    return Depends(rate_limit("provision_trial", limit=10, window_seconds=3600))


def payment_init_limit() -> Any:
    return Depends(rate_limit("pay_init", limit=30, window_seconds=60))


def public_form_limit() -> Any:
    return Depends(rate_limit("public_form", limit=10, window_seconds=600))


def webhook_limit() -> Any:
    """IP-keyed throttle for payment-provider callbacks (Payme/Click/subscription).

    Generous on purpose — providers legitimately retry, and a stuck transaction
    can fire many times — but bounded so a spoofed caller can't hammer the
    O(N)-tenant-scan auth (one linear secret match per request). Fails open like
    the rest of the limiter, so a Redis outage never blocks a real settlement.
    """
    return Depends(rate_limit("webhook", limit=600, window_seconds=60))


# ── (SEC-003) Bounded limits for the high-cost / high-abuse route set ────────
# Deliberately NOT a blanket limiter over every route: a low global ceiling would
# throttle normal multi-widget UI fan-out, and no traffic evidence justifies one.
# These four buckets cover only the endpoints whose per-request cost or abuse
# value is high, and every ceiling is set well above realistic interactive use.
#
# All four fail OPEN. They protect shared capacity, not a credential, so a Redis
# outage must not lock operators out of the product; the degraded mode is already
# logged and counted. The stricter fail-CLOSED policy on auth/signup is unchanged.
def report_limit() -> Any:
    """Aggregate report reads. The reports screen mounts ~6 widgets at once, so a
    full reload costs ~6; 60/min leaves an order of magnitude of headroom."""
    return Depends(rate_limit("report", limit=60, window_seconds=60))


def pdf_limit() -> Any:
    """PDF renders (WeasyPrint CPU). User-initiated one document at a time;
    30/min is ~10x a brisk operator printing contracts back to back."""
    return Depends(rate_limit("pdf", limit=30, window_seconds=60))


def import_limit() -> Any:
    """Bulk CSV/XLSX parse and commit. A deliberate, infrequent action —
    10 per 5 minutes covers preview-then-commit retries comfortably."""
    return Depends(rate_limit("import", limit=10, window_seconds=300))


def search_limit() -> Any:
    """Text search over customers/installments. Applied ONLY when `q` is
    non-empty (see `search_limit_if_query`), so paging an unfiltered list is
    never limited. 90/min absorbs debounced type-ahead with room to spare."""
    return Depends(rate_limit("search", limit=90, window_seconds=60))


# ── Amplification + external-cost surfaces (cache/ratelimit audit, 2026-08-12) ──
# The four buckets above bound EXPENSIVE work. These bound work that leaves the
# building: a message to a real person, an object into storage, a dump of
# somebody's data. The cost of abuse here is not our CPU — it is Telegram
# delivery quota, storage spend, and being the thing that spams a shop's
# customers. None of these carried any limit at all.
#
# All fail OPEN, for the same reason the expensive-route set does: they guard
# shared capacity, not a credential, and every one of them already sits behind
# authentication and a role check. A Redis blip must not stop an operator
# inviting a customer. The degraded path stays logged and counted.
def broadcast_limit() -> Any:
    """Fan-out to many recipients at once — one request, N Telegram messages.

    The highest amplification factor in the product, and the only surface where a
    single authorised mistake reaches an entire customer base. Deliberate and
    infrequent by nature: 10/hour is far above any real campaign cadence and far
    below anything that could be used to spam through us.
    """
    return Depends(rate_limit("broadcast", limit=10, window_seconds=3600))


def invite_limit() -> Any:
    """Minting or re-sending an invitation — each one sends a message.

    Sized for the real burst this has: a shop onboarding its existing customer
    list in one sitting. 60/hour covers that comfortably while bounding a loop
    that would otherwise use us to message arbitrary people.
    """
    return Depends(rate_limit("invite", limit=60, window_seconds=3600))


def upload_limit() -> Any:
    """Binary uploads (product images, KYC scans). Storage + egress cost, and the
    scanning/validation path is the most expensive per-request work we do on
    untrusted bytes. 60 per 5 minutes is well past a bulk catalogue session."""
    return Depends(rate_limit("upload", limit=60, window_seconds=300))


def export_limit() -> Any:
    """Bulk dumps of personal data (customer export, DSAR export).

    Rate-limited because each one assembles and returns a whole person's record:
    expensive to produce, and the single most useful endpoint to an attacker who
    has obtained a session and wants everything at once. 20 per 10 minutes.
    """
    return Depends(rate_limit("export", limit=20, window_seconds=600))


def search_limit_if_query() -> Any:
    """Rate-limit a list endpoint only when it is actually running a text search.

    An unfiltered list read is cheap and is left alone; a `q=` search scans, so it
    gets the search bucket. Keeping the condition here means the route signature
    and its response contract are untouched.
    """
    _inner = rate_limit("search", limit=90, window_seconds=60)

    async def dependency(request: Request) -> None:
        q = request.query_params.get("q")
        if q and q.strip():
            await _inner(request)

    dependency._ratelimit_bucket = "search"        # type: ignore[attr-defined]
    dependency._ratelimit_fail_closed = False      # type: ignore[attr-defined]
    dependency._ratelimit_conditional = "q"        # type: ignore[attr-defined]
    return Depends(dependency)
