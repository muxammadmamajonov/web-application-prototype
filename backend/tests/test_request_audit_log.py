"""#757 — every request leaves a structured audit-trail line.

RequestContextMiddleware now emits a ``request.completed`` log on every non-probe
request, carrying the response status, the real client IP (X-Forwarded-For first
hop), and the best-effort authenticated user id. This covers endpoints the
business AuditLog (specific mutating actions only) doesn't, satisfying the OWASP
"complete request/response audit trail" requirement.

DB-free: rides the ASGITransport ``async_client``; ``/`` and ``/health`` need no DB.
"""
from __future__ import annotations

import logging

import httpx


def _completion_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "request.completed"]


async def test_request_completion_logged_with_status_and_ip(
    async_client: httpx.AsyncClient, caplog
) -> None:
    with caplog.at_level(logging.INFO, logger="muddato.observability"):
        resp = await async_client.get("/", headers={"X-Forwarded-For": "9.9.9.9, 10.0.0.1"})
    assert resp.status_code == 200
    recs = _completion_records(caplog)
    assert recs, "every non-probe request must emit a request.completed audit line"
    rec = recs[-1]
    assert rec.status_code == 200
    assert rec.client_ip == "9.9.9.9"  # XFF first hop, not the proxy
    assert rec.user_id is None  # anonymous request


async def test_request_completion_captures_authenticated_user(
    async_client: httpx.AsyncClient, caplog
) -> None:
    from app.core.security import issue_token

    token = issue_token(user_id="user-abc-123")
    with caplog.at_level(logging.INFO, logger="muddato.observability"):
        await async_client.get("/", headers={"Authorization": f"Bearer {token}"})
    recs = _completion_records(caplog)
    assert recs and recs[-1].user_id == "user-abc-123"


async def test_probe_paths_excluded_from_audit_log(
    async_client: httpx.AsyncClient, caplog
) -> None:
    with caplog.at_level(logging.INFO, logger="muddato.observability"):
        await async_client.get("/health")
    assert not _completion_records(caplog), "/health is an infra probe — not audited"
