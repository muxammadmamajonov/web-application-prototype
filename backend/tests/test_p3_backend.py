"""P3 backend — #101 HTML-escape, #105 cron-run auth, #106 cron logging,
#100 SoftDeleteMixin removal.

DB-free: render() escaping, the cron-run authorization pure function, and source
guards for the bot escaping + the demoted per-row log + the removed mixin.
"""
from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

# ── #101: escape user-controlled values in HTML messages ──────────────────────

def test_render_escapes_values_but_not_template_html() -> None:
    from app.services.notifications import render

    out = render("Hello <b>{name}</b>!", name="A<b>&B")
    # The template's own <b> tags stay; only the substituted value is escaped.
    assert out == "Hello <b>A&lt;b&gt;&amp;B</b>!"


def test_render_handles_none_value() -> None:
    from app.services.notifications import render

    assert render("Hi {name}", name=None) == "Hi "


def test_bot_greeting_escapes_first_name() -> None:
    import app.bot.main as bot

    src = inspect.getsource(bot)
    assert "html.escape(message.from_user.first_name)" in src


# ── #105: cron run/{job_id} is super-admin only (+ optional secret) ──────────

def _user(role: object):
    from app.models import User

    u = User(telegram_id=1)
    u.platform_role = role  # type: ignore[assignment]
    return u


def test_cron_run_requires_super_admin() -> None:
    from app.api.cron import _authorize_cron_run
    from app.models.enums import PlatformRole

    with pytest.raises(HTTPException) as ei:
        _authorize_cron_run(_user(PlatformRole.support), None)
    assert ei.value.status_code == 403

    # super-admin, no CRON_SECRET configured → allowed
    _authorize_cron_run(_user(PlatformRole.super_admin), None)


def test_cron_secret_enforced_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.cron import _authorize_cron_run
    from app.config import settings
    from app.models.enums import PlatformRole

    monkeypatch.setattr(settings, "cron_secret", "s3cret")
    su = _user(PlatformRole.super_admin)
    with pytest.raises(HTTPException):
        _authorize_cron_run(su, None)        # missing header
    with pytest.raises(HTTPException):
        _authorize_cron_run(su, "wrong")     # bad header
    _authorize_cron_run(su, "s3cret")        # correct → allowed


def test_run_endpoint_uses_super_admin_authz() -> None:
    from app.api.cron import run_job_now

    src = inspect.getsource(run_job_now)
    assert "_authorize_cron_run" in src
    assert "require_platform_admin" not in src  # no longer the looser check


# ── #106: per-row cron log demoted to debug ──────────────────────────────────

def test_sms_fallback_logs_at_debug() -> None:
    import app.services.scheduler as sched

    src = inspect.getsource(sched)
    assert 'log.debug("cron.sms_fallback' in src
    assert 'log.info("cron.sms_fallback' not in src


# ── #100: SoftDeleteMixin removed ────────────────────────────────────────────

def test_soft_delete_mixin_removed() -> None:
    import app.models.base as base

    assert not hasattr(base, "SoftDeleteMixin")
    assert "SoftDeleteMixin" not in base.__all__
