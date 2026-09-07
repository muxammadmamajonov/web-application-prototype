"""FastAPI entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from sqlalchemy.exc import DataError, IntegrityError

from app.config import settings
from app.core.health import readiness
from app.core.limits import MaxBodySizeMiddleware
from app.core.observability import (
    CSRFValidationMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
    configure_logging,
    init_sentry,
    setup_metrics,
)

# (#70) Structured JSON logging + Sentry, wired before anything logs.
configure_logging()
init_sentry()
logger = logging.getLogger("muddato")

# Enable detailed SQLAlchemy logging during profiling (PROFILING_MODE=true)
import os

if os.getenv("PROFILING_MODE") == "true":
    logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.pool").setLevel(logging.DEBUG)
    logger.info("profiling.mode enabled — SQLAlchemy query logging activated")


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("muddato.start env=%s frontend=%s", settings.environment, settings.frontend_url)
    # (#82) The scheduler runs in its own worker process (app/worker.py). Web
    # machines set RUN_SCHEDULER=false so they don't each boot APScheduler; the
    # Postgres lease lock still guards overlap. Default True keeps a
    # single-process / dev deploy working out of the box.
    scheduler_on = settings.run_scheduler
    if scheduler_on:
        from app.services.scheduler import setup_scheduler
        try:
            await setup_scheduler()
        except Exception:
            logger.exception("scheduler.boot_failed — running without background jobs")
    else:
        logger.info("scheduler.disabled — handled by the worker process (RUN_SCHEDULER=false)")
    # (#89) DB/Redis pool + idle-in-transaction saturation sampler — see
    # app/core/pool_metrics.py for why this closes the MUDD-86 observability gap.
    from app.core.pool_metrics import start as start_pool_metrics
    from app.core.pool_metrics import stop as stop_pool_metrics
    pool_metrics_task = start_pool_metrics()
    yield
    await stop_pool_metrics(pool_metrics_task)
    if scheduler_on:
        from app.services.scheduler import shutdown_scheduler
        try:
            await shutdown_scheduler()
        except Exception:
            logger.exception("scheduler.shutdown_failed")
    # (#77) Release pooled DB connections + the Redis client on shutdown so a
    # `fly deploy` rollover doesn't leak connections against the pooler limit.
    try:
        from app.db import engine
        await engine.dispose()
    except Exception:
        logger.exception("engine.dispose_failed")
    try:
        from app.core.redis import close_redis
        await close_redis()
    except Exception:
        logger.exception("redis.close_failed")
    try:
        from app.api.bot import close_bots
        await close_bots()
    except Exception:
        logger.exception("bot.close_failed")
    try:
        from app.services.notifications import close_bot as close_notification_bot
        await close_notification_bot()
    except Exception:
        logger.exception("notification_bot.close_failed")
    logger.info("muddato.stop")


app = FastAPI(
    title="Muddato API",
    version="0.1.0",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)

# (#88) Reject an oversized request body on its Content-Length before it is read.
app.add_middleware(CSRFValidationMiddleware)
app.add_middleware(MaxBodySizeMiddleware)
# (#70) Baseline security headers + per-request correlation id, just inside CORS
# so they stamp every app/inner-middleware response (including error responses).
app.add_middleware(RequestContextMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
# (audit) CORS is added LAST so it runs OUTERMOST. Otherwise an error response
# short-circuited by an outer middleware (CSRF 403 / body-size 413) bypasses CORS
# and reaches the browser WITHOUT CORS headers — the browser then surfaces an
# opaque CORS failure instead of the real 403/413. Outermost CORS wraps them all.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    # (#97) Explicit, not wildcard. Wildcard methods/headers with credentials is
    # spec-incompatible (browsers reject "*" + credentials) and looser than
    # needed — these are the verbs + headers the SPA actually sends.
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Tenant-Id"],
)

# (#70) RED metrics at /metrics when the instrumentator is installed (no-op
# otherwise) — registered before routes are included.
setup_metrics(app)


# (#87) Map known unique-constraint names to stable, user-facing 409 codes.
_INTEGRITY_CODES = {
    "uq_customer_tenant_phone": "customer_phone_exists",
    "uq_guarantor_tenant_phone": "guarantor_phone_exists",
    "uq_product_tenant_sku": "product_sku_exists",
    "uq_invoice_tenant_period": "invoice_period_exists",
    # (r96) Platform-wide identity. These were missing, so "this number already
    # belongs to someone" arrived as a bare `conflict` and the client rendered
    # the generic "this data has changed, refresh the page" — advice that cannot
    # possibly help, for a problem the user can actually fix.
    "uq_users_phone_digits": "phone_already_registered",
    "uq_users_email_lower": "email_already_registered",
}


def _constraint_name(exc: BaseException) -> str | None:
    """The violated constraint's name, dug out from under the driver wrapper.

    (QA M-2) This used to be a single `getattr(exc.orig, "constraint_name")`,
    which is right for psycopg and wrong for asyncpg — and asyncpg is what we
    run. There, `exc.orig` is `AsyncAdapt_asyncpg_dbapi.IntegrityError`, a
    wrapper with no such attribute, and the real `UniqueViolationError` hangs
    off its `__cause__`:

        exc.orig           -> wrapper, constraint_name absent
        exc.orig.__cause__ -> UniqueViolationError, constraint_name='uq_…'

    So the lookup silently missed every time, every mapped code fell through to
    the generic `conflict`, and the client told people to refresh the page when
    they had simply typed a phone number someone else already had. Walking the
    chain keeps working under either driver rather than trading one for the
    other.
    """
    seen = 0
    err: BaseException | None = getattr(exc, "orig", None) or exc
    while err is not None and seen < 5:   # bounded: __cause__ chains can loop
        name = getattr(err, "constraint_name", None)
        if name:
            return str(name)
        err = getattr(err, "__cause__", None)
        seen += 1
    return None


@app.exception_handler(IntegrityError)
async def _integrity_error_handler(request: Request, exc: IntegrityError) -> ORJSONResponse:
    """(#87) A unique-constraint violation becomes a clean 409 with a stable code
    instead of a generic 500. The session is already rolled back by get_db's error
    path. An unknown constraint gets a generic ``conflict`` — still a 409, never a
    mislabeled duplicate or a 500."""
    constraint = _constraint_name(exc)
    code = _INTEGRITY_CODES.get(constraint or "", "conflict")
    logger.warning("integrity.conflict path=%s constraint=%s", request.url.path, constraint)
    return ORJSONResponse(status_code=409, content={"detail": code})


@app.exception_handler(DataError)
async def _data_error_handler(request: Request, exc: DataError) -> ORJSONResponse:
    """(#810) A DB-level *data* violation — a value too long for its column, a
    numeric overflow, a bad cast — becomes a clean 422 instead of a generic 500.

    This is the safety net behind the per-field schema bounds: even if one is
    missed, or a cross-field product (e.g. ``qty * unit_price``) overflows the
    column precision, the client gets an actionable validation error rather than
    an opaque 500. ``get_db``'s error path has already rolled the session back. We
    log only the underlying error *type* + path — never ``exc`` itself, which can
    echo the offending value (possible PII)."""
    logger.warning(
        "data_error.invalid path=%s err=%s",
        request.url.path, type(getattr(exc, "orig", exc)).__name__,
    )
    return ORJSONResponse(status_code=422, content={"detail": "invalid_field_value"})


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    """Convert any uncaught exception into a structured JSON 500 instead of the
    bare ``Internal Server Error`` plaintext, so the frontend can surface a real
    message and the traceback lands in the logs for debugging."""
    logger.exception("unhandled.error path=%s method=%s", request.url.path, request.method)
    return ORJSONResponse(status_code=500, content={"detail": "internal_error"})


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    """Liveness — the process is up (no dependency checks)."""
    return {"status": "ok", "env": settings.environment}


@app.get("/ready", tags=["meta"])
async def ready() -> ORJSONResponse:
    """Readiness — DB is critical; Redis is reported as degraded."""
    ok, checks = await readiness()
    return ORJSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ready" if ok else "not_ready", **checks},
    )


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    return {"name": settings.project_name, "frontend": settings.frontend_url}


from app.api.router import api_router

app.include_router(api_router, prefix="/api")
