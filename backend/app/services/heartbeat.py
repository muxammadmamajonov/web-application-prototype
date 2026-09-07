"""Dead-man's switch: prod tells the outside world it is alive.

Every monitor Muddato had before this one worked the same way — something
reached *in* to check production, and raised the alarm when a check failed:

* ``synthetic-monitor.yml`` runs inside GitHub Actions and files a GitHub issue.
* ``deploy.yml`` surfaced failure as a red run nobody was watching.

That shape has a hole, and 2026-08-26 walked straight into it: GitHub Actions
had a major outage from ~16:36. The monitor stopped running AND the incident
channel (a GitHub issue) stopped working, both at once, because they share a
failure domain. Production happened to be healthy. Had it gone down in that
window, nothing would have told anyone.

**This inverts the dependency.** Nothing reaches in. The app reaches out, on a
schedule, and pings a URL — but only while it can actually serve. An external
dead-man's-switch service (Healthchecks.io, BetterStack Heartbeats, Cron
monitoring in UptimeRobot — any of them, free tier) alerts when the pings STOP.

Why that matters: a check that must *succeed at detecting failure* can itself
fail silently. A dead-man's switch cannot. Silence is the alarm, so every
failure mode reports:

===========================  ==========================  ====================
what breaks                  old monitor                 this
===========================  ==========================  ====================
app crashed / OOM            detects (if Actions is up)  detects — no ping
DB unreachable               detects (if Actions is up)  detects — no ping
Fly region down              detects (if Actions is up)  detects — no ping
machine network-partitioned  detects (if Actions is up)  detects — no ping
**GitHub Actions down**      **blind**                   detects — no ping
**Mac mini runner down**     **blind**                   detects — no ping
===========================  ==========================  ====================

What it deliberately does NOT cover: user-facing reachability. The app can be
perfectly alive while DNS, TLS or the Cloudflare edge in front of muddato.uz is
broken, and this heartbeat would keep firing. That needs a probe from outside,
pointed at the public URL — see docs/OBSERVABILITY.md. The two are complements,
not alternatives, and neither replaces the other.
"""
from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.core.health import readiness

log = logging.getLogger("muddato.heartbeat")

#: Short on purpose. A hung or slow dead-man's-switch endpoint must never hold
#: a scheduler worker, and a missed ping is not itself an emergency — the
#: external grace period (set it to several times the ping interval) absorbs a
#: single failure. Only a *sustained* silence should page anyone.
_TIMEOUT_SECONDS = 5.0


async def emit_heartbeat_job() -> None:
    """Ping the dead-man's switch — but ONLY while this process can serve.

    The `readiness()` gate is the whole point. Pinging unconditionally would
    turn this into a process-liveness check: "the Python interpreter is running"
    is not the same claim as "Muddato can take a payment". A machine that is up
    with an unreachable database must trip the switch, not hold it open.

    Redis is deliberately not a gate. `readiness()` treats Redis as degraded but
    still servable (one flaky cache must not eject every machine from the load
    balancer at once); this follows the same rule so the two never disagree.

    Never raises. A scheduler job that can throw is a scheduler that can stop,
    and a monitoring feature that takes down the thing it monitors is worse than
    no monitoring at all.
    """
    url = settings.heartbeat_url
    if not url:
        # Unconfigured is a valid state (local dev, CI, review apps). Debug, not
        # warning — warning-level noise on every interval trains people to
        # ignore the log this feature depends on.
        log.debug("heartbeat.skipped_unconfigured")
        return

    try:
        can_serve, checks = await readiness()
    except Exception:
        # A readiness check that itself blew up means we cannot claim health.
        # Withhold the ping — fail closed, the same direction as everything else.
        log.warning("heartbeat.readiness_raised_withholding_ping", exc_info=True)
        return

    if not can_serve:
        # THE IMPORTANT BRANCH. Do not ping. Do not "ping with a failure flag" —
        # that would need the network to work in exactly the moment things are
        # broken. Silence travels through a dead network; a message does not.
        log.error("heartbeat.withheld_not_ready checks=%s", checks)
        return

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.get(url)
        log.debug("heartbeat.sent status=%s", resp.status_code)
    except Exception:
        # Losing one ping is survivable (see the grace-period note above) and
        # must not escalate into a scheduler error event.
        log.warning("heartbeat.send_failed", exc_info=True)
