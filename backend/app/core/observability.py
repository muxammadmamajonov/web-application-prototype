"""Observability wiring (#70): structured JSON logging, request-correlation
IDs, error reporting (Sentry), security headers, and RED metrics.

Everything here is defensive: optional integrations (Sentry, Prometheus) are
guarded behind import/availability checks so the app boots fine without the
extra packages, and the logging setup falls back to stdlib ``basicConfig`` if
``structlog`` is unavailable. Nothing in this module should ever be able to
crash startup.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, ClassVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import settings

log = logging.getLogger("muddato.observability")

# Header names a request id may arrive under (Fly injects Fly-Request-Id).
_RID_HEADERS = ("fly-request-id", "x-request-id", "x-correlation-id")

# (#757) Infra probes are high-frequency and carry no audit value — keep them out
# of the per-request audit log so it isn't drowned in health/metrics noise.
_AUDIT_SKIP_PATHS = frozenset({"/health", "/ready", "/metrics", "/readiness"})


def _client_ip(request: Request) -> str:
    """Best-effort real client IP. Behind the Cloudflare Pages proxy + Fly the
    X-Forwarded-For first hop is closest to the caller."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    fly = request.headers.get("fly-client-ip")
    if fly:
        return fly
    return request.client.host if request.client else "unknown"


def _request_subject(request: Request) -> str | None:
    """Best-effort authenticated user id from the bearer/cookie JWT, for the
    per-request audit line. Never raises — an anonymous or bad-token request just
    records ``user_id=None`` (no DB lookup; this is a log field, not auth)."""
    token = None
    authz = request.headers.get("authorization", "")
    if authz.lower().startswith("bearer "):
        token = authz.split(" ", 1)[1]
    if not token:
        from app.core.security import SESSION_COOKIE_NAME
        token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    try:
        from app.core.security import decode_token
        return decode_token(token).get("sub")
    except Exception:
        return None


def configure_logging() -> None:
    """Route stdlib logging through structlog's JSON renderer so every existing
    ``logging.getLogger(...)`` call emits a structured line that carries the
    request-scoped contextvars (request_id, path, method). Falls back to plain
    stdlib logging if structlog isn't installed or anything goes wrong."""
    level = settings.log_level.upper()

    # httpx logs "HTTP Request: <method> <url> ..." at INFO for EVERY outbound
    # call, full query string included, and several call sites carry a
    # credential in the URL itself: OIDC token exchange, the Eskiz SMS
    # provider, signed media URLs, the heartbeat ping. At the app's own INFO
    # level those lines reach prod logs verbatim — observed, not assumed.
    #
    # WARNING drops httpx's per-call line and keeps its warnings (retries,
    # pool exhaustion), which is the right trade: the dropped line duplicates
    # what our own structlog request context already records, with request_id
    # and timing attached, so nothing is lost for incident response — while
    # everything in it is a secret in at least one case.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        import structlog

        # Pretty console in dev, JSON everywhere else.
        json_logs = settings.is_production or settings.environment.lower() != "development"
        shared: list[Any] = [
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
        ]
        renderer = (
            structlog.processors.JSONRenderer()
            if json_logs
            else structlog.dev.ConsoleRenderer()
        )
        structlog.configure(
            processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )
        formatter = structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root = logging.getLogger()
        root.handlers.clear()
        root.addHandler(handler)
        root.setLevel(level)
        log.info("logging.configured", extra={"renderer": "json" if json_logs else "console"})
    except Exception:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        logging.getLogger("muddato").warning("structlog unavailable — using stdlib logging")


def init_sentry() -> None:
    """Initialize Sentry when a DSN is configured and the SDK is installed."""
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            traces_sample_rate=0.1,
            send_default_pii=False,  # never ship PII to Sentry
            release=getattr(settings, "release", None) or "muddato@dev",
        )
        log.info("sentry.initialized env=%s", settings.environment)
    except ImportError:
        log.warning("sentry_dsn set but sentry-sdk is not installed — `uv add sentry-sdk`")
    except Exception:
        log.exception("sentry.init_failed")


def setup_metrics(app: Any) -> None:
    """Expose Prometheus RED metrics at /metrics when the instrumentator is
    installed. No-op otherwise.

    (audit) NOTE: /metrics is intentionally UNAUTHENTICATED. Fly's managed
    Prometheus scrapes it on the app port over the private 6PN network (see
    fly.toml ``[metrics] port=8000 path=/metrics``) and sends no auth header, so
    token-gating here would blank the Grafana dashboards + alert rules. The
    endpoint IS also reachable on the direct ``*.fly.dev`` hostname (the CF proxy
    only forwards ``/api/*``, so it is NOT reachable via muddato.uz), but the RED
    series carry no PII/secrets — only request counts/latencies/status/paths.
    To fully close the public path, move metrics to a dedicated internal-only
    port (separate listener) — tracked as a follow-up; not worth a managed-
    Prometheus regression here."""
    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
        log.info("metrics.exposed path=/metrics")
    except ImportError:
        log.info("prometheus-fastapi-instrumentator not installed — /metrics disabled")
    except Exception:
        log.exception("metrics.setup_failed")


def start_worker_metrics_server(port: int) -> None:
    """Expose the default Prometheus registry over HTTP from a non-web process
    (the scheduler worker). Cron-health metrics (muddato_cron_*) only exist in
    that process, so Fly needs them served from the worker machine too. No-op
    when ``port`` is 0/unset or prometheus_client is unavailable; never raises."""
    if not port:
        return
    try:
        from prometheus_client import start_http_server

        start_http_server(port)
        log.info("worker.metrics.exposed port=%d path=/metrics", port)
    except Exception:
        log.exception("worker.metrics.setup_failed")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind a per-request correlation id (+ path/method) into the structlog
    contextvars so every log line emitted during the request is correlatable,
    and echo it back as ``X-Request-Id`` for client-side tracing."""

    async def dispatch(self, request: Request, call_next):
        rid = None
        for h in _RID_HEADERS:
            rid = request.headers.get(h)
            if rid:
                break
        rid = rid or uuid.uuid4().hex

        try:
            import structlog

            structlog.contextvars.clear_contextvars()
            structlog.contextvars.bind_contextvars(
                request_id=rid, path=request.url.path, method=request.method
            )
        except Exception:
            # structlog absent (the documented stdlib fallback) or a contextvar
            # fault: the request proceeds and still gets its X-Request-Id header —
            # only the per-line correlation fields are lost. Debug because this
            # would otherwise fire on every single request.
            log.debug("observability.log_context.bind_failed", exc_info=True)

        # (#89) In-flight gauge: paired inc/dec around the ENTIRE downstream
        # chain (routing, DB calls, business logic), so a request stuck behind a
        # saturated DB pool shows up here immediately — it doesn't need to finish
        # (or even reach the duration histogram) to be counted. The `finally`
        # guarantees the pair balances even if a handler raises past `call_next`.
        from app.core.metrics import dec_http_inflight, inc_http_inflight

        inc_http_inflight()
        try:
            response = await call_next(request)
        finally:
            dec_http_inflight()
        response.headers["X-Request-Id"] = rid

        # (#757) Per-request audit line so EVERY request leaves a correlatable
        # trace — not just the specific mutations the business AuditLog records.
        # request_id/path/method ride the contextvars above; status, the real
        # client IP, and the best-effort authenticated user are attached here.
        if request.url.path not in _AUDIT_SKIP_PATHS:
            log.info(
                "request.completed",
                extra={
                    "status_code": response.status_code,
                    "client_ip": _client_ip(request),
                    "user_id": _request_subject(request),
                },
            )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline security headers on every API response. The JSON API isn't
    framed and serves no HTML, so a strict frame/sniff posture is safe; the SPA
    gets its full CSP via Cloudflare Pages ``_headers`` (#73)."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


class CSRFValidationMiddleware(BaseHTTPMiddleware):
    """(#746/#843) CSRF protection for COOKIE-authenticated state changes.

    For state-changing requests (POST, PUT, DELETE, PATCH) that carry the session
    cookie, require a valid double-submit ``X-CSRF-Token`` header that matches the
    readable ``muddato.csrf`` cookie (#257). A cross-site request can neither read that
    cookie (same-origin policy) nor set a custom header (CORS blocks it without a
    preflight the server won't grant), so a matching pair can't be forged — which,
    combined with the SameSite session cookie, defends the ambient-cookie credential.

    (#843 + followup) CSRF only applies to that ambient cookie, so a request is EXEMPT
    when it isn't relying on it: one carrying a Bearer token (the localStorage frontend +
    SDKs — the Authorization header is its credential and can't be set cross-origin, even
    when the browser also auto-sends a stale cookie), or one with no session cookie at all.
    The session-establishing login endpoints (``/api/auth/telegram*``, signup,
    provision-trial) are exempt too — they're authenticated by the Telegram hash and can't
    carry a CSRF token before the user has a session, so a stale cookie must not block
    re-login. Safe methods (GET/HEAD/OPTIONS) and health/metrics/webhooks are also exempt.
    """

    # Methods that modify state and require CSRF protection
    CSRF_METHODS: ClassVar[set[str]] = {"POST", "PUT", "DELETE", "PATCH"}
    
    # Paths that are exempt from CSRF validation (health checks, webhooks, etc)
    CSRF_EXEMPT_PATHS: ClassVar[set[str]] = {
        "/health",
        "/metrics",
        "/readiness",
        "/api/webhooks",  # Webhooks are authenticated via signature, not cookies
        # Session-establishing endpoints (#843-followup): authenticated by the Telegram
        # auth hash, NOT the cookie, and a CSRF token can't exist before the user has a
        # session — so a returning user whose browser still holds a stale session cookie
        # must not be CSRF-blocked from (re-)logging in. Covers /telegram, /telegram/oauth,
        # /telegram/webapp, signup and trial provisioning via the startswith match.
        "/api/auth/telegram",
        "/api/auth/signup",
        "/api/auth/provision-trial",
    }

    async def dispatch(self, request: Request, call_next):
        # Only validate CSRF for state-changing requests
        if request.method not in self.CSRF_METHODS:
            return await call_next(request)
        
        # Skip CSRF validation for exempt paths
        if any(request.url.path.startswith(p) for p in self.CSRF_EXEMPT_PATHS):
            return await call_next(request)

        # (#843 + followup) CSRF protects the AMBIENT session cookie — the credential a
        # browser auto-sends, which a cross-site request could exploit. Two kinds of
        # request are NOT relying on that cookie, so they are exempt:
        #   1. one carrying a Bearer token (the localStorage frontend + SDKs): auth is
        #      Bearer-first, and an attacker can't set the Authorization header from a
        #      cross-site request, so it can't be CSRF-forged even when the browser also
        #      auto-sends a (now-vestigial) session cookie alongside it;
        #   2. one with no session cookie at all: there is no ambient credential to exploit.
        # Without this the middleware 403s every Bearer mutation that happens to carry a
        # leftover cookie (and re-login while a stale cookie is set), breaking the frontend.
        from app.core.security import SESSION_COOKIE_NAME
        authz = request.headers.get("authorization", "")
        if authz.lower().startswith("bearer ") or SESSION_COOKIE_NAME not in request.cookies:
            return await call_next(request)

        # (#257) Cookie-authenticated state change → require a VALID double-submit CSRF
        # token: the X-CSRF-Token header must equal the readable `muddato.csrf` cookie.
        # A cross-site attacker can neither read that cookie (same-origin policy) nor set
        # the custom header, so it cannot produce a matching pair. This upgrades the prior
        # header-presence-only check (which any value satisfied) to real verification.
        # (#746) Always return a JSONResponse, never raise: an HTTPException raised inside a
        # BaseHTTPMiddleware escapes FastAPI's ExceptionMiddleware and surfaces as a 500.
        import hmac

        from app.core.security import CSRF_COOKIE_NAME
        header_token = request.headers.get("X-CSRF-Token")
        cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
        log = logging.getLogger("muddato.csrf")
        if not header_token:
            log.warning("csrf.missing_header method=%s path=%s", request.method, request.url.path)
            return JSONResponse(status_code=403, content={"detail": "missing_csrf_token"})
        if not cookie_token or not hmac.compare_digest(header_token, cookie_token):
            log.warning(
                "csrf.invalid_token method=%s path=%s have_cookie=%s",
                request.method, request.url.path, bool(cookie_token),
            )
            return JSONResponse(status_code=403, content={"detail": "invalid_csrf_token"})

        return await call_next(request)
