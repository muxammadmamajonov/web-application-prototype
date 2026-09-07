"""Phase 5 Pass 3 — TR-018 (silently swallowed exceptions) + TR-019 (boundary logging).

Two invariants, asserted together for every site touched by this pass:

  1. **Behaviour is preserved.** Each of the ten ``except Exception: pass`` sites guards
     *instrumentation* or a *best-effort cache*, never business logic. When the inner call
     raises, the surrounding operation must still succeed / fall back exactly as it did
     before — no new raise, no changed return, no changed control flow.
  2. **The failure is no longer invisible.** The same site must now emit a breadcrumb with a
     stable event name at the level appropriate to the boundary (``debug`` for the tolerated
     instrumentation faults, ``info``/``error`` for the TR-019 operational boundaries).

Plus two safety properties the pass must not violate:

  * **No secrets or PII in any emitted record** — most sharply at
    ``ratelimit._identity``, whose input is an attacker-controlled ``Authorization`` header.
  * **No duplicate/lost event on failure** — a metrics fault inside an error path must not
    swallow the operational warning that path already emits, nor emit a success line.

DB-free by construction: every case drives the target function directly (or through the
existing ``_FakeDB``-style harness in ``test_payment_webhooks.py``), so this file runs
identically with or without ``TEST_DATABASE_URL``.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from decimal import Decimal

import httpx
import pytest
from starlette.requests import Request

# ── helpers ───────────────────────────────────────────────────────────────────


def _events(caplog, name: str) -> list[logging.LogRecord]:
    """Records whose formatted message starts with the stable event token."""
    return [r for r in caplog.records if r.getMessage().startswith(name)]


def _boom(*_a, **_k):
    raise RuntimeError("instrumentation exploded")


def _fake_request(headers: dict[str, str]) -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("203.0.113.7", 4242),
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    })


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Result:
    def __init__(self, val):
        self._val = val

    def scalar_one_or_none(self):
        return self._val

    def first(self):
        """The row form, for call sites that select COLUMNS rather than an entity.

        (Batch 5 independent-review correction) The Click provider lookup moved
        into ``core.webhook_identity``, which selects
        ``(tenant_id, click_secret_key)`` and reads it with ``.first()`` instead
        of loading a whole ``TenantSettings``. A scripted ``_Obj`` is projected
        onto that pair so the same fixtures keep working.
        """
        if self._val is None or isinstance(self._val, tuple):
            return self._val
        tenant_id = getattr(self._val, "tenant_id", None)
        secret = getattr(self._val, "click_secret_key", None) or getattr(
            self._val, "payme_secret_key", None,
        )
        return (tenant_id, secret)


class _FakeDB:
    """Async stand-in returning a scripted sequence of rows (last one repeats)."""

    def __init__(self, *vals):
        self._vals = list(vals) or [None]
        self.commits = 0

    async def execute(self, *_a, **_k):
        val = self._vals.pop(0) if len(self._vals) > 1 else self._vals[0]
        return _Result(val)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


# ══════════════════════════════════════════════════════════════════════════════
# TR-018 — the ten silent catches
# ══════════════════════════════════════════════════════════════════════════════

# ── site 1 — app/api/reports.py::_cached (Redis read) ─────────────────────────

def test_report_cache_read_fault_still_computes_and_leaves_a_breadcrumb(
    monkeypatch, caplog,
) -> None:
    """A Redis fault on the report cache read must fall through to compute (not
    raise, not return stale), and must say so."""
    from app.api import reports

    class _DeadRedis:
        async def get(self, _key):
            raise ConnectionError("redis down")

        async def set(self, *_a, **_k):
            raise ConnectionError("redis down")

    monkeypatch.setattr(reports, "get_redis", lambda: _DeadRedis())

    async def _compute():
        return {"rows": 3}

    with caplog.at_level(logging.DEBUG, logger="muddato.reports"):
        out = asyncio.run(reports._cached(
            "11111111-1111-1111-1111-111111111111", "aging", "p=1", _compute,
        ))

    assert out == {"rows": 3}, "cache fault must fall through to a fresh compute"
    assert _events(caplog, "report.cache.read_failed"), (
        "a swallowed cache-read fault must leave a breadcrumb"
    )


# ── site 2 — app/api/tenant.py::_bind_tenant_log ──────────────────────────────

def test_tenant_log_bind_fault_is_tolerated_and_recorded(monkeypatch, caplog) -> None:
    """Binding the tenant into the log context is decoration; if it fails the
    request must proceed, but the loss of tenant attribution must be visible."""
    import structlog

    from app.api import tenant

    monkeypatch.setattr(structlog.contextvars, "bind_contextvars", _boom)

    with caplog.at_level(logging.DEBUG, logger="muddato.tenant"):
        assert tenant._bind_tenant_log(
            "22222222-2222-2222-2222-222222222222",
        ) is None, "a log-binding fault must never raise into the request path"

    assert _events(caplog, "tenant.log_context.bind_failed")


# ── sites 3 & 4 — app/core/breaker.py (metrics) ───────────────────────────────

def test_breaker_state_transition_survives_a_metrics_fault(monkeypatch, caplog) -> None:
    """A Prometheus fault must never stop the circuit breaker from changing state —
    the breaker is the availability control, the metric is only its shadow."""
    from app.core import breaker as bmod

    monkeypatch.setattr("app.core.metrics.record_breaker_state", _boom)
    br = bmod.CircuitBreaker("probe", failure_threshold=2)

    with caplog.at_level(logging.DEBUG, logger="muddato.breaker"):
        br._set_state(bmod.OPEN)

    assert br._state == bmod.OPEN, "state must still transition when metrics fail"
    assert _events(caplog, "breaker.metrics.state_record_failed")


def test_breaker_trips_open_even_when_the_trip_metric_fails(monkeypatch, caplog) -> None:
    from app.core import breaker as bmod

    monkeypatch.setattr("app.core.metrics.record_breaker_trip", _boom)
    br = bmod.CircuitBreaker("probe2", failure_threshold=1)

    with caplog.at_level(logging.DEBUG, logger="muddato.breaker"):
        br.record_failure()

    assert br._state == bmod.OPEN, "breaker must open regardless of the metric"
    assert not br.allow(), "an open breaker must still refuse calls"
    assert _events(caplog, "breaker.metrics.trip_record_failed")


# ── site 5 — app/core/cache.py::_record ───────────────────────────────────────

def test_cache_metric_fault_does_not_escape(monkeypatch, caplog) -> None:
    from app.core import cache as cmod

    monkeypatch.setattr("app.core.metrics.record_cache", _boom)

    with caplog.at_level(logging.DEBUG, logger="muddato.cache"):
        assert cmod._record("plans", "hit") is None

    assert _events(caplog, "cache.metrics.record_failed")


# ── site 6 — app/core/observability.py::RequestContextMiddleware ──────────────

async def test_request_survives_a_log_context_bind_fault(
    async_client: httpx.AsyncClient, monkeypatch, caplog,
) -> None:
    """The correlation-id binding is best-effort; a structlog fault must still leave
    the response (and its X-Request-Id) intact, but must not vanish."""
    import structlog

    monkeypatch.setattr(structlog.contextvars, "bind_contextvars", _boom)

    with caplog.at_level(logging.DEBUG, logger="muddato.observability"):
        resp = await async_client.get("/")

    assert resp.status_code == 200, "a logging fault must never fail the request"
    assert resp.headers.get("X-Request-Id"), "the correlation id must still be echoed"
    assert _events(caplog, "observability.log_context.bind_failed")


# ── site 7 — app/core/ratelimit.py::_identity (SECURITY-SENSITIVE) ────────────

def test_undecodable_bearer_falls_back_to_ip_without_leaking_the_token(caplog) -> None:
    """The rate-limit key derivation reads an *attacker-controlled* header. A garbage
    token must (a) fall back to the IP bucket exactly as before, (b) leave a breadcrumb,
    and (c) never put the token — or any part of it — into a log record."""
    from app.core import ratelimit as rl

    secret = "eyJhbGciOiJIUzI1NiJ9.FORGED-TOKEN-MATERIAL.sig"
    req = _fake_request({
        "authorization": f"Bearer {secret}",
        "fly-client-ip": "198.51.100.9",
    })

    with caplog.at_level(logging.DEBUG, logger="muddato.ratelimit"):
        identity = rl._identity(req)

    assert identity == "ip:198.51.100.9", "an invalid token must fall back to the IP bucket"
    assert _events(caplog, "ratelimit.identity.token_undecodable")

    blob = "\n".join(f"{r.getMessage()}{getattr(r, 'args', '')}" for r in caplog.records)
    assert secret not in blob, "the raw token must never be logged"
    for chunk in secret.split("."):
        assert chunk not in blob, f"no token segment may be logged (leaked {chunk!r})"


def test_identity_breadcrumb_is_debug_not_warning(caplog) -> None:
    """Deliberate level choice: this path is driven by unauthenticated callers, so a
    warning would be an attacker-triggerable log flood. It must stay below INFO."""
    from app.core import ratelimit as rl

    req = _fake_request({"authorization": "Bearer not-a-jwt", "fly-client-ip": "1.1.1.1"})
    with caplog.at_level(logging.DEBUG, logger="muddato.ratelimit"):
        rl._identity(req)

    recs = _events(caplog, "ratelimit.identity.token_undecodable")
    assert recs and all(r.levelno < logging.INFO for r in recs), (
        "an attacker-controlled path must not emit INFO or above"
    )


# ── site 8 — app/core/ratelimit.py degraded-path metrics ──────────────────────

def test_degraded_limiter_still_warns_when_its_own_metric_fails(monkeypatch, caplog) -> None:
    """The metrics failure inside the Redis-down branch must not swallow the
    ``ratelimit.redis_unavailable`` warning that branch exists to emit, and must not
    change the fail-open decision."""
    from app.core import ratelimit as rl

    monkeypatch.setattr(rl, "get_redis", _boom)
    monkeypatch.setattr("app.core.metrics.record_ratelimit_degraded", _boom)

    dep = rl.rate_limit("probe", limit=5, window_seconds=60)  # fail_closed=False
    req = _fake_request({"fly-client-ip": "192.0.2.5"})

    with caplog.at_level(logging.DEBUG, logger="muddato.ratelimit"):
        assert asyncio.run(dep(req)) is None, "fail-open must still allow the request"

    assert _events(caplog, "ratelimit.redis_unavailable"), (
        "the operational warning must survive a metrics fault in the same branch"
    )
    assert _events(caplog, "ratelimit.metrics.degraded_record_failed")


# ── sites 9 & 10 — app/core/metrics.py pool gauges ────────────────────────────

@pytest.mark.parametrize(
    ("fn_name", "gauge_attr", "event"),
    [
        ("record_db_pool_stats", "_db_active_connections", "metrics.db_pool.record_failed"),
        ("record_redis_pool_stats", "_redis_active_connections", "metrics.redis_pool.record_failed"),
    ],
)
def test_pool_gauge_fault_is_swallowed_but_recorded(
    monkeypatch, caplog, fn_name: str, gauge_attr: str, event: str,
) -> None:
    """These run on every request from db.py/redis.py — a gauge fault must stay
    non-fatal, and its breadcrumb must stay at debug so it cannot flood."""
    from app.core import metrics as m

    if not m._ENABLED:
        pytest.skip("prometheus_client unavailable — gauges are no-ops")

    monkeypatch.setattr(m, gauge_attr, _Obj(set=_boom))

    with caplog.at_level(logging.DEBUG, logger="muddato.metrics"):
        assert getattr(m, fn_name)(4, 10) is None

    recs = _events(caplog, event)
    assert recs, "a swallowed gauge fault must leave a breadcrumb"
    assert all(r.levelno < logging.INFO for r in recs), (
        "a per-request path must not log at INFO or above"
    )


# ── the register itself ───────────────────────────────────────────────────────

def test_no_silent_pass_only_broad_except_remains_in_backend_app() -> None:
    """TR-018's exit criterion, enforced as a guard: no ``except Exception``/bare
    handler in ``backend/app`` may have an effective body of only ``pass``.

    AST-based on purpose — a text grep for ``except Exception:\\n    pass`` misses the
    two sites carrying a trailing ``# pragma: no cover`` comment (that is exactly the
    10-vs-8 discrepancy this pass reconciled).
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            caught = ast.unparse(node.type) if node.type else "bare"
            if caught not in {"Exception", "BaseException", "bare"}:
                continue
            body = [
                s for s in node.body
                if not (isinstance(s, ast.Expr)
                        and isinstance(s.value, ast.Constant)
                        and isinstance(s.value.value, str))
            ]
            if len(body) == 1 and isinstance(body[0], ast.Pass):
                offenders.append(f"{path.relative_to(root.parent)}:{node.lineno}")

    assert not offenders, (
        "broad exception handlers whose only body is `pass` swallow failures silently; "
        f"narrow them or log them: {offenders}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# TR-019 — operational boundary logging
# ══════════════════════════════════════════════════════════════════════════════

# ── payme: perform / cancel state transitions ─────────────────────────────────

def _payme_settings_and_payment(status):
    from app.models.enums import PaymentStatus

    ts = _Obj(tenant_id="tenant-A")
    pay = _Obj(
        tenant_id="tenant-A", external_ref="tx-9", id="pay-9",
        installment_id="inst-9", amount=Decimal("100"), status=status,
        paid_at=None, approved_at=None, method=None, provider_payload=None,
    )
    del PaymentStatus
    return ts, pay


def _open_installment(*, principal: Decimal = Decimal("1000")):
    from app.models.enums import InstallmentStatus

    return _Obj(
        status=InstallmentStatus.active,
        principal=principal,
        paid_amount=Decimal("0"),
        monthly_amount=Decimal("100"),
        months=10,
        paid_count=0,
        payoff_discount_amount=None,
        resched_base_paid=None,
        resched_base_count=0,
        resched_base_due_date=None,
        start_date=date.today(),
        next_due_date=date.today(),
        closed_at=None,
    )


def test_payme_perform_logs_the_commit_transition(caplog) -> None:
    from app.api.webhooks import payme
    from app.models.enums import PaymentStatus

    ts, pay = _payme_settings_and_payment(PaymentStatus.pending)
    db = _FakeDB(pay, _open_installment())  # payment lookup, then installment lookup

    with caplog.at_level(logging.INFO, logger="muddato.webhooks.payme"):
        res = asyncio.run(payme._perform_transaction(db, ts, "rpc-1", {"id": "tx-9"}))

    assert "error" not in res, "the transaction must still succeed"
    assert pay.status == PaymentStatus.approved
    recs = _events(caplog, "payment.transition")
    assert recs, "committing a payment must leave an operational breadcrumb"
    msg = recs[-1].getMessage()
    assert "provider=payme" in msg
    assert "from=pending to=approved" in msg

    # The approved/refunded guards return early, so `rejected` is the other prior
    # status this branch can see — it must be reported, not assumed away.
    caplog.clear()
    ts2, pay2 = _payme_settings_and_payment(PaymentStatus.rejected)
    with caplog.at_level(logging.INFO, logger="muddato.webhooks.payme"):
        asyncio.run(payme._perform_transaction(
            _FakeDB(pay2, _open_installment()), ts2, "rpc-2", {"id": "tx-9"},
        ))

    msg2 = _events(caplog, "payment.transition")[-1].getMessage()
    assert "from=rejected to=approved" in msg2, (
        f"prior status must be read, not assumed pending; got: {msg2!r}"
    )


def test_payme_cancel_logs_the_reject_transition(caplog) -> None:
    from app.api.webhooks import payme
    from app.models.enums import PaymentStatus

    ts, pay = _payme_settings_and_payment(PaymentStatus.approved)
    db = _FakeDB(pay, None)

    with caplog.at_level(logging.INFO, logger="muddato.webhooks.payme"):
        res = asyncio.run(payme._cancel_transaction(db, ts, "rpc-1", {"id": "tx-9"}))

    assert "error" not in res
    assert pay.status == PaymentStatus.refunded
    recs = _events(caplog, "payment.transition")
    assert recs and "from=approved to=refunded" in recs[-1].getMessage()

    # Only the refunded guard returns early, so pending and rejected both reach here.
    caplog.clear()
    ts2, pay2 = _payme_settings_and_payment(PaymentStatus.rejected)
    with caplog.at_level(logging.INFO, logger="muddato.webhooks.payme"):
        asyncio.run(payme._cancel_transaction(_FakeDB(pay2, None), ts2, "rpc-2", {"id": "tx-9"}))

    msg2 = _events(caplog, "payment.transition")[-1].getMessage()
    assert "from=rejected to=refunded" in msg2, (
        f"prior status must be read, not derived from was_completed alone; got: {msg2!r}"
    )


def test_payme_idempotent_replay_emits_no_second_transition(caplog) -> None:
    """A redelivered perform on an already-approved payment changes nothing, so it
    must NOT emit a second commit line — otherwise the breadcrumb over-counts money."""
    import datetime as _dt

    from app.api.webhooks import payme
    from app.models.enums import PaymentStatus

    ts, pay = _payme_settings_and_payment(PaymentStatus.approved)
    pay.paid_at = _dt.datetime(2026, 1, 1, tzinfo=_dt.UTC)

    with caplog.at_level(logging.INFO, logger="muddato.webhooks.payme"):
        res = asyncio.run(payme._perform_transaction(_FakeDB(pay), ts, "rpc-1", {"id": "tx-9"}))

    assert "error" not in res
    assert not _events(caplog, "payment.transition"), (
        "an idempotent replay is not a state transition"
    )


def test_payme_rejected_transaction_emits_no_success_transition(caplog) -> None:
    """A cross-tenant (rejected) perform must not emit a commit line."""
    from app.api.webhooks import payme

    ts = _Obj(tenant_id="tenant-A")
    foreign = _Obj(tenant_id="tenant-B", external_ref="tx-9", id="pay-9")

    with caplog.at_level(logging.INFO, logger="muddato.webhooks.payme"):
        res = asyncio.run(payme._perform_transaction(_FakeDB(foreign), ts, "rpc-1", {"id": "tx-9"}))

    assert "error" in res
    assert not _events(caplog, "payment.transition")


# ── click: complete approves / cancels ────────────────────────────────────────

def _click_complete(monkeypatch, *, error: str, payment_status):
    from app.api.webhooks import click

    monkeypatch.setattr(click, "decrypt_secret", lambda v: "sekret")
    ts = _Obj(tenant_id="tenant-A", click_service_id="svc", click_secret_key="CIPHERTEXT-BLOB")
    pay = _Obj(
        tenant_id="tenant-A", id="00000000-0000-0000-0000-0000000000aa",
        installment_id="inst-1", amount=Decimal("50"), status=payment_status,
        paid_at=None, approved_at=None, method=None,
    )
    mtid = "00000000-0000-0000-0000-0000000000aa"
    sign = click._click_sign(
        click_trans_id="ct-1", service_id="svc", secret_key="sekret",
        merchant_trans_id=mtid, merchant_prepare_id="mp-1",
        amount="50.00", action="1", sign_time="2026-01-01 00:00:00",
    )
    db = _FakeDB(ts, pay, _open_installment())  # settings, payment, installment
    res = asyncio.run(click.complete(
        click_trans_id="ct-1", service_id="svc", merchant_trans_id=mtid,
        merchant_prepare_id="mp-1", amount="50.00", action="1",
        error=error, error_note="", sign_time="2026-01-01 00:00:00",
        sign_string=sign, db=db,
    ))
    return res, pay


def test_click_complete_logs_the_approval_transition(monkeypatch, caplog) -> None:
    """The approval guard is ``status != approved``, so the prior status can be any of
    pending / rejected / refunded. ``from=`` must report which one it actually was."""
    from app.models.enums import PaymentStatus

    with caplog.at_level(logging.INFO, logger="muddato.webhooks.click"):
        res, pay = _click_complete(monkeypatch, error="0", payment_status=PaymentStatus.pending)

    assert res["error_note"] == "OK"
    assert pay.status == PaymentStatus.approved
    recs = _events(caplog, "payment.transition")
    assert recs, "an approved Click payment must leave a breadcrumb"
    assert "provider=click" in recs[-1].getMessage()
    assert "from=pending to=approved" in recs[-1].getMessage()

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="muddato.webhooks.click"):
        _res2, pay2 = _click_complete(
            monkeypatch, error="0", payment_status=PaymentStatus.rejected,
        )

    assert pay2.status == PaymentStatus.approved
    msg2 = _events(caplog, "payment.transition")[-1].getMessage()
    assert "from=rejected to=approved" in msg2, (
        f"prior status must be read, not assumed pending; got: {msg2!r}"
    )


def test_click_user_cancellation_logs_the_refund_transition(monkeypatch, caplog) -> None:
    """``from=`` must be the payment's ACTUAL prior status — a value of the closed
    PaymentStatus enum — not a hardcoded literal and not the cancellation *reason*
    (the reason is already the response's ``error_note``).

    Both prior states are exercised in one case so a hardcoded literal cannot pass:
    ``pending`` is the common path, ``approved`` is the cancel-after-approval path
    that this branch also serves (it runs before the idempotency guard below).
    """
    from app.models.enums import PaymentStatus

    with caplog.at_level(logging.INFO, logger="muddato.webhooks.click"):
        res, pay = _click_complete(monkeypatch, error="-9", payment_status=PaymentStatus.pending)

    assert res["error_note"] == "cancelled_by_user"
    assert pay.status == PaymentStatus.refunded
    recs = _events(caplog, "payment.transition")
    assert recs, "a user cancellation must leave a breadcrumb"
    msg = recs[-1].getMessage()
    assert "from=pending to=refunded" in msg, (
        f"expected the real prior status, got: {msg!r}"
    )
    assert "cancelled_by_user" not in msg, (
        "the cancellation reason is not a PaymentStatus and must not sit in from="
    )

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="muddato.webhooks.click"):
        _res2, pay2 = _click_complete(
            monkeypatch, error="-9", payment_status=PaymentStatus.approved,
        )

    assert pay2.status == PaymentStatus.refunded
    msg2 = _events(caplog, "payment.transition")[-1].getMessage()
    assert "from=approved to=refunded" in msg2, (
        f"prior status must be read, not assumed; got: {msg2!r}"
    )


def test_click_idempotent_replay_emits_no_second_transition(monkeypatch, caplog) -> None:
    """(MUDD-59 M-05) A redelivery of an already-approved payment must not emit a
    second transition line — and, since it never re-approves anything, must reply
    Click's own "already paid" error (-4) rather than the same ``OK`` a brand-new
    approval gets (see `test_mudd59_h6_m05_click_webhook.py` for the direct
    behavioral proof; this file only re-asserts the observability half)."""
    from app.api.webhooks.click import ERR_ALREADY_PAID
    from app.models.enums import PaymentStatus

    with caplog.at_level(logging.INFO, logger="muddato.webhooks.click"):
        res, _pay = _click_complete(monkeypatch, error="0", payment_status=PaymentStatus.approved)

    assert res["error"] == ERR_ALREADY_PAID
    assert res["error_note"] == "already_paid"
    assert not _events(caplog, "payment.transition")


def test_payment_transition_logs_carry_no_secret_or_signature(monkeypatch, caplog) -> None:
    """The Click webhook body carries a signature derived from the merchant secret.
    Neither the secret, its stored ciphertext, nor the signature may reach a log line."""
    from app.api.webhooks import click
    from app.models.enums import PaymentStatus

    expected_sign = click._click_sign(
        click_trans_id="ct-1", service_id="svc", secret_key="sekret",
        merchant_trans_id="00000000-0000-0000-0000-0000000000aa",
        merchant_prepare_id="mp-1", amount="50.00", action="1",
        sign_time="2026-01-01 00:00:00",
    )

    with caplog.at_level(logging.DEBUG):
        _res, _pay = _click_complete(monkeypatch, error="0", payment_status=PaymentStatus.pending)

    assert _events(caplog, "payment.transition"), "guard is vacuous without a transition line"
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "sekret" not in blob, "the decrypted merchant secret must never be logged"
    assert "CIPHERTEXT-BLOB" not in blob, "the stored ciphertext must never be logged"
    assert expected_sign not in blob, "the request signature must never be logged"


# ── subscription top-up credit ────────────────────────────────────────────────

def test_subscription_credit_logs_the_commit(monkeypatch, caplog) -> None:
    from app.api.webhooks import subscription as sub

    topup = _Obj(
        id="00000000-0000-0000-0000-0000000000bb", tenant_id="tenant-A",
        status="pending", amount=Decimal("500"), method="payme",
    )

    async def _mark_paid(_db, t, **_k):
        t.status = "paid"
        return True

    async def _audit(*_a, **_k):
        return None

    monkeypatch.setattr(sub.sc, "mark_topup_paid", _mark_paid)
    monkeypatch.setattr(sub.audit, "record", _audit)

    with caplog.at_level(logging.INFO, logger="muddato.webhooks.subscription"):
        out = asyncio.run(sub._credit_by_topup_id_status(
            _FakeDB(topup), str(topup.id), provider_ref="pr-1",
            captured_amount=Decimal("500"),
        ))

    assert out == sub.CREDIT_OK, "the credit outcome must be unchanged"
    recs = _events(caplog, "payment.transition")
    assert recs, "crediting a top-up must leave a breadcrumb"
    assert "to=paid" in recs[-1].getMessage()


def test_subscription_refusal_emits_no_commit_line(caplog) -> None:
    from app.api.webhooks import subscription as sub

    with caplog.at_level(logging.INFO, logger="muddato.webhooks.subscription"):
        out = asyncio.run(sub._credit_by_topup_id_status(
            _FakeDB(None), "not-a-uuid", provider_ref=None, captured_amount=Decimal("1"),
        ))

    assert out == sub.CREDIT_REFUSED
    assert not _events(caplog, "payment.transition"), "a refusal is not a commit"


# ── scheduler job failure boundary ────────────────────────────────────────────

def test_cron_job_failure_is_logged_not_only_metered(caplog) -> None:
    """A crashed job previously left *only* a Prometheus counter — no log line at all.
    The failure boundary must name the job and the error class."""
    from app.services import scheduler as sch

    event = _Obj(job_id="apply_late_fees", exception=ValueError("kaboom"), retval=None)

    with caplog.at_level(logging.INFO, logger="muddato.scheduler"):
        sch._record_job_event(event)

    recs = _events(caplog, "cron.job_failed")
    assert recs, "a failed cron job must leave a log breadcrumb, not just a counter"
    msg = recs[-1].getMessage()
    assert "apply_late_fees" in msg
    assert "ValueError" in msg, "the error class aids triage"
    assert "kaboom" not in msg, "the raw exception message may carry payload detail"
    assert recs[-1].levelno >= logging.ERROR


def test_cron_success_and_lock_skip_emit_no_failure_line(caplog) -> None:
    """Only the failure path may log here — a successful or lock-skipped run must not,
    or the boundary becomes per-run noise duplicating each job's own summary line."""
    from app.services import scheduler as sch

    with caplog.at_level(logging.DEBUG, logger="muddato.scheduler"):
        sch._record_job_event(_Obj(job_id="j1", exception=None, retval={"flipped": 2}))
        sch._record_job_event(_Obj(job_id="j2", exception=None, retval={"skipped": "locked"}))

    assert not _events(caplog, "cron.job_failed")


def test_cron_failure_log_cannot_break_the_listener_if_the_log_backend_dies() -> None:
    """TR-019's constraint: added logging must never break the path it observes.

    Models the realistic outage — the log *sink* failing under a normal StreamHandler
    (broken pipe to the shipper / full disk). ``StreamHandler.emit`` routes such I/O
    faults to ``Handler.handleError`` rather than propagating, so the cron listener
    still completes. Pinned rather than assumed.

    Note this is specifically an I/O-fault guarantee: stdlib does *not* shield a caller
    from a handler that raises arbitrarily out of ``emit``. That is why the added line
    is a plain ``log.error`` with a pre-validated format string and no lazy ``__str__``
    on its arguments — nothing in it can raise on the formatting path.
    """
    from app.services import scheduler as sch

    class _BrokenStream:
        def write(self, _s):
            raise OSError("log shipper unreachable")

        def flush(self):
            raise OSError("log shipper unreachable")

    logger = logging.getLogger("muddato.scheduler")
    handler = logging.StreamHandler(_BrokenStream())
    logger.addHandler(handler)
    try:
        sch._record_job_event(_Obj(job_id="j3", exception=RuntimeError("x"), retval=None))
        sch._record_job_event(_Obj(job_id="j4", exception=None, retval=None))
    finally:
        logger.removeHandler(handler)
