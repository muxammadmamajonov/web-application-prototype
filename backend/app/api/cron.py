"""Admin-only endpoints for inspecting + triggering background jobs.

These are guarded by `platform_role IN ('super_admin', 'support')` — never
exposed to tenants. Used for:

  * **dev / staging**: trigger the daily cron manually instead of waiting
    until 03:00 Asia/Tashkent.
  * **prod incident**: re-run the reminder job after a Telegram outage
    blip; the idempotency guards inside each job make this safe.
  * **observability**: list registered jobs + their next fire times.
"""
from __future__ import annotations

import hmac
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.config import settings
from app.core.authz import require_platform_admin
from app.core.dbauth import worker_authority
from app.core.security import current_user
from app.models import User
from app.models.enums import PlatformRole
from app.services.scheduler import (
    apply_late_fees_job,
    daily_owner_briefing_job,
    default_reminder_templates,
    get_scheduler,
    mark_overdue_installments_job,
    publish_risk_signals_job,
    purge_expired_pii_job,
    send_reminders_job,
)

#: (Batch 5) Internal cron trigger: scans every tenant, authenticated by the cron secret.
#: The DB authority is granted by the ROUTE, explicitly — never inferred
#: from a missing tenant header.
router = APIRouter(prefix="/admin/cron", tags=["admin"], dependencies=[Depends(worker_authority)])
log = logging.getLogger("muddato.cron.api")


def _authorize_cron_run(user: User, cron_secret: str | None) -> None:
    """(#105) Firing platform-wide financial/notification jobs is super-admin
    only — `support` can list/inspect but not run them. When a CRON_SECRET is
    configured (for an external scheduler), the X-Cron-Secret header must also
    match (constant-time)."""
    if user.platform_role != PlatformRole.super_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "super_admin_only")
    configured = settings.cron_secret
    if configured and not (cron_secret and hmac.compare_digest(cron_secret, configured)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "bad_cron_secret")


@router.get("/jobs")
async def list_jobs(user: Annotated[User, Depends(current_user)]) -> dict:
    """Return registered jobs + their next fire time (Tashkent local)."""
    require_platform_admin(user)
    try:
        sched = get_scheduler()
    except RuntimeError:
        return {"running": False, "jobs": []}
    return {
        "running": sched.running,
        "jobs": [
            {
                "id": j.id,
                "next_run_time": j.next_run_time.isoformat() if j.next_run_time else None,
                "trigger": str(j.trigger),
            }
            for j in sched.get_jobs()
        ],
    }


@router.post("/run/{job_id}")
async def run_job_now(
    job_id: str,
    user: Annotated[User, Depends(current_user)],
    x_cron_secret: Annotated[str | None, Header(alias="X-Cron-Secret")] = None,
) -> dict:
    """Manually run one of the recurring jobs. Super-admin only (plus the
    X-Cron-Secret header when one is configured). Idempotent — the jobs guard
    against duplicate effects."""
    _authorize_cron_run(user, x_cron_secret)

    runners = {
        "mark_overdue_installments": mark_overdue_installments_job,
        "apply_late_fees": apply_late_fees_job,
        "publish_risk_signals": publish_risk_signals_job,
        "send_reminders": send_reminders_job,
        "daily_owner_briefing": daily_owner_briefing_job,
        "purge_expired_pii": purge_expired_pii_job,
    }
    if job_id not in runners:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown_job: {job_id}")
    log.info("cron.manual_trigger by=%s job=%s", user.id, job_id)
    result = await runners[job_id]()
    return {"job_id": job_id, "result": result}


@router.get("/reminder-templates/defaults")
async def reminder_templates_defaults(user: Annotated[User, Depends(current_user)]) -> dict:
    """Canonical UZ default reminder templates — used by the Settings UI
    as the "Reset to default" target."""
    require_platform_admin(user)
    return default_reminder_templates()
