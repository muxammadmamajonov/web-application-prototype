"""Phase 9 M-1 — the self-serve tenant/trial funnel is closed; the demo form is not.

Phase 2 classified self-serve onboarding (ONB-001/-002) and the trial lifecycle
as LATER, while keeping the public demo request form (MKT-004) and the
operator-created tenant + owner-claim link. Until this pass, three entry points
still provisioned a tenant on their own:

    POST /demo/intent  → Redis intent → bot `/start demo_<slug>` → tenant
    POST /auth/signup                                            → tenant
    POST /auth/provision-trial                                   → tenant

These tests pin the closed state *and* the retained one: the form must still
capture a lead and hand it to the existing Muddato support channel for human
follow-up, and the operator path must be untouched.

Transport is always mocked — no test here talks to Telegram.
"""
from __future__ import annotations

import pytest
from aiogram.exceptions import TelegramAPIError

LEAD = {
    "business_name": "Demo Electronics",
    "owner_name": "Sardor Mamatov",
    # The canonical field name. It may carry a phone or an email — see
    # `test_p9p2_lead_capture_contract.py`, which also covers the `phone` alias.
    "contact": "+998901234567",
    "plan_code": "lite",
    "locale": "uz",
    "referrer": "https://muddato.uz/uz",
}


@pytest.fixture
def sent(monkeypatch):
    """Capture support-channel deliveries instead of performing them."""
    from app.services import support_channel

    calls: list[dict] = []

    class _FakeBot:
        async def send_message(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(support_channel, "_bot", _FakeBot())
    return calls


@pytest.fixture
def failing_transport(monkeypatch):
    from app.services import support_channel

    class _DeadBot:
        async def send_message(self, **kwargs):
            raise TelegramAPIError(method=None, message="channel unavailable")

    monkeypatch.setattr(support_channel, "_bot", _DeadBot())


# ── A. The demo request is lead capture, and nothing more ────────────────────

@pytest.mark.asyncio
async def test_demo_request_succeeds_and_reaches_the_support_channel(async_client, sent):
    r = await async_client.post("/api/demo/intent", json=LEAD)
    assert r.status_code < 300, r.text
    assert len(sent) == 1, "the lead must reach the support channel exactly once"
    text = sent[0]["text"]
    assert "Demo Electronics" in text
    assert "Sardor Mamatov" in text
    assert "+998901234567" in text


@pytest.mark.asyncio
async def test_demo_response_hands_back_no_provisioning_handle(async_client, sent):
    """No slug, no deep link, no trial length — following a link must not exist."""
    r = await async_client.post("/api/demo/intent", json=LEAD)
    body = r.json()
    for forbidden in ("slug", "telegram_url", "trial_days", "expires_in_seconds"):
        assert forbidden not in body, f"{forbidden} still handed to the browser"
    assert "t.me" not in r.text


@pytest.mark.asyncio
async def test_demo_request_escapes_html_before_it_reaches_telegram(async_client, sent):
    hostile = {**LEAD, "business_name": "Ali & <b>Vali</b>", "owner_name": "<script>x</script>"}
    r = await async_client.post("/api/demo/intent", json=hostile)
    assert r.status_code < 300
    text = sent[0]["text"]
    assert "<b>Vali</b>" not in text
    assert "<script>" not in text
    assert "&amp;" in text, "a bare & breaks Telegram HTML parse mode"


@pytest.mark.asyncio
async def test_demo_request_has_stable_behaviour_when_the_transport_fails(
    async_client, failing_transport
):
    r = await async_client.post("/api/demo/intent", json=LEAD)
    assert r.status_code == 502
    assert r.json()["detail"] == "telegram_send_failed"


@pytest.mark.asyncio
async def test_demo_request_leaks_no_pii_into_logs(async_client, sent, caplog):
    import logging

    with caplog.at_level(logging.DEBUG):
        await async_client.post("/api/demo/intent", json=LEAD)
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "+998901234567" not in blob
    assert "Sardor Mamatov" not in blob


def test_demo_request_keeps_its_public_form_rate_limit():
    """The rate-limit contract is unchanged by this pass."""
    from app.main import app

    route = next(r for r in app.routes if getattr(r, "path", None) == "/api/demo/intent")
    buckets = [
        getattr(d.dependency, "_ratelimit_bucket", None) for d in route.dependencies
    ]
    assert "public_form" in buckets


@pytest.mark.asyncio
async def test_demo_request_creates_no_tenant_subscription_or_membership(isolation_ctx, sent):
    """DB tier: a real submission against a real schema adds no provisioning rows."""
    from sqlalchemy import func, select

    from app.db import get_db
    from app.main import app
    from app.models import Tenant, TenantSubscription, TenantUser, User

    client, _ = isolation_ctx
    session_factory = app.dependency_overrides[get_db]

    async def counts() -> tuple[int, ...]:
        agen = session_factory()
        db = await agen.__anext__()
        try:
            out = []
            for model in (Tenant, TenantSubscription, TenantUser, User):
                out.append((await db.execute(select(func.count()).select_from(model))).scalar_one())
            return tuple(out)
        finally:
            await agen.aclose()

    before = await counts()
    r = await client.post("/api/demo/intent", json=LEAD)
    assert r.status_code < 300, r.text
    assert await counts() == before, "the demo form provisioned something"


# ── B. The bot deep link cannot provision ────────────────────────────────────

def test_the_bot_cannot_reach_tenant_provisioning():
    """Structural: the handler module holds no provisioning capability at all."""
    import app.bot.main as bot_main

    assert not hasattr(bot_main, "provision_tenant_from_intent")
    assert not hasattr(bot_main, "consume_intent")


def test_the_demo_module_holds_no_redeemable_intent():
    """The Redis intent store existed only to feed provisioning; it is gone."""
    import app.api.demo as demo

    for gone in ("consume_intent", "peek_intent", "INTENT_TTL_SECONDS", "_make_slug"):
        assert not hasattr(demo, gone), f"{gone} still redeemable"


@pytest.mark.asyncio
async def test_a_demo_deep_link_provisions_nothing_and_stays_safe(isolation_ctx):
    """`/start demo_<anything>` — expired, used or unknown — is one safe path now."""
    from sqlalchemy import func, select

    from app.bot.main import cmd_start
    from app.db import get_db
    from app.main import app
    from app.models import Tenant, TenantSubscription, TenantUser

    _, _ = isolation_ctx
    session_factory = app.dependency_overrides[get_db]

    async def provisioning_rows() -> tuple[int, ...]:
        """What a redemption used to create. Compared before/after rather than
        against a literal, so the assertion stays true whatever the fixture
        seeds and cannot pass by coincidence when that seed changes."""
        agen = session_factory()
        db = await agen.__anext__()
        try:
            out = []
            for model in (Tenant, TenantUser, TenantSubscription):
                out.append((await db.execute(select(func.count()).select_from(model))).scalar_one())
            return tuple(out)
        finally:
            await agen.aclose()

    replies: list[str] = []

    class _Msg:
        from_user = type("U", (), {"id": 55501, "username": "u", "first_name": "Dilnoza",
                                   "last_name": None, "language_code": "uz"})()

        async def answer(self, text, **kw):
            replies.append(text)

    class _Cmd:
        args = "demo_deadbeef"

    before = await provisioning_rows()
    await cmd_start(_Msg(), _Cmd())
    assert await provisioning_rows() == before, "the deep link provisioned something"
    assert replies, "the user got no answer at all"


# ── C. /auth/signup and /auth/provision-trial are disabled ───────────────────

@pytest.mark.asyncio
async def test_signup_is_disabled(async_client):
    r = await async_client.post("/api/auth/signup", json={
        "id": 1, "hash": "x", "auth_date": 1, "business_name": "Acme",
    })
    assert r.status_code == 403
    assert r.json()["detail"] == "self_serve_provisioning_disabled"


@pytest.mark.asyncio
async def test_signup_refuses_before_it_validates_or_verifies_anything(async_client):
    """An empty body would be a 422 and a bad hash a 401 — the gate must win both.

    That ordering is the proof that no HMAC check, no user upsert and no
    provisioning runs on a disabled path.
    """
    r = await async_client.post("/api/auth/signup", json={})
    assert r.status_code == 403, "body validation ran before the scope gate"


@pytest.mark.asyncio
async def test_provision_trial_is_disabled(async_client):
    r = await async_client.post(
        "/api/auth/provision-trial",
        json={"business_name": "Acme"},
        headers={"Authorization": "Bearer garbage"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "self_serve_provisioning_disabled"


@pytest.mark.asyncio
async def test_provision_trial_refuses_before_authenticating(async_client):
    """No bearer token at all still yields the gate's 403, not current_user's 401."""
    r = await async_client.post("/api/auth/provision-trial", json={"business_name": "Acme"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_the_disabled_paths_persist_nothing(isolation_ctx):
    from sqlalchemy import func, select

    from app.db import get_db
    from app.main import app
    from app.models import Tenant, TenantSubscription, TenantUser, User

    client, ctx = isolation_ctx
    session_factory = app.dependency_overrides[get_db]

    async def counts() -> tuple[int, ...]:
        agen = session_factory()
        db = await agen.__anext__()
        try:
            out = []
            for model in (Tenant, TenantSubscription, TenantUser, User):
                out.append((await db.execute(select(func.count()).select_from(model))).scalar_one())
            return tuple(out)
        finally:
            await agen.aclose()

    before = await counts()
    await client.post("/api/auth/signup", json={
        "id": 999, "hash": "x", "auth_date": 1, "business_name": "Ghost Shop",
    })
    await client.post("/api/auth/provision-trial",
                      json={"business_name": "Ghost Shop"}, headers=ctx["auth_a"])
    assert await counts() == before


def test_the_disabled_paths_keep_their_rate_limiters():
    """Disabling must not quietly drop the throttles these routes shipped with.

    They are retained, not enforced-first: flipping the scope flag back on must
    restore a fully throttled route without re-deriving the limits.
    """
    from app.main import app

    for path, bucket in (
        ("/api/auth/signup", "signup"),
        ("/api/auth/provision-trial", "provision_trial"),
    ):
        route = next(r for r in app.routes if getattr(r, "path", None) == path)
        buckets = [getattr(d.dependency, "_ratelimit_bucket", None) for d in route.dependencies]
        assert bucket in buckets, f"{path} lost its {bucket} limiter"


def test_the_refusal_does_not_depend_on_redis():
    """The gate is declared before the limiter, so the status is one value.

    ``signup_limit`` is fail-closed: ordered first it would answer 503 whenever
    Redis is unavailable and 403 otherwise, which is not a stable contract — and
    it would spend a Redis round-trip per request on a route that refuses
    unconditionally.
    """
    from app.core.scope import _deny_self_serve_provisioning
    from app.main import app

    for path in ("/api/auth/signup", "/api/auth/provision-trial"):
        route = next(r for r in app.routes if getattr(r, "path", None) == path)
        deps = [d.dependency for d in route.dependencies]
        gate = deps.index(_deny_self_serve_provisioning)
        limiter = next(i for i, d in enumerate(deps) if hasattr(d, "_ratelimit_bucket"))
        assert gate < limiter, f"{path} throttles before it refuses"


# ── F. Scope guard — fails if any of the three paths re-opens ────────────────
#
# Structural, not a file snapshot: each assertion names one capability, so
# refactoring the modules is free and restoring provisioning is not. The copy
# half of this guard is `frontend/src/i18n/publicTrialPromise.test.ts`.

def test_the_scope_flag_is_off_so_every_gate_below_is_really_closed():
    from app.core.scope import SELF_SERVE_PROVISIONING_ENABLED

    assert SELF_SERVE_PROVISIONING_ENABLED is False


def test_both_auth_provisioning_routes_declare_the_gate():
    from app.core.scope import _deny_self_serve_provisioning
    from app.main import app

    for path in ("/api/auth/signup", "/api/auth/provision-trial"):
        route = next(r for r in app.routes if getattr(r, "path", None) == path)
        assert _deny_self_serve_provisioning in [d.dependency for d in route.dependencies], (
            f"{path} no longer sits behind the scope gate"
        )


def _code_symbols(module) -> tuple[set[str], set[str]]:
    """Names and string literals a module actually *executes*.

    Read off the AST with docstrings dropped, so the comments and docstrings that
    explain what these paths used to do — rationale worth keeping — cannot fail
    the guard, and only real references can.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(module))
    docstrings = {
        node.body[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    names, literals = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node not in docstrings:
            literals.add(node.value)
    return names, literals


def test_no_public_or_bot_entry_point_can_provision_a_tenant():
    """The provisioning helper must be reachable only from the gated routes."""
    import app.api.demo as demo
    import app.bot.main as bot_main

    for module in (demo, bot_main):
        names, literals = _code_symbols(module)
        assert "provision_tenant_from_intent" not in names, f"{module.__name__} can provision"
        assert "consume_intent" not in names, f"{module.__name__} can redeem an intent"
        assert not [s for s in literals if "demo_" in s], (
            f"{module.__name__} still handles a demo deep link"
        )


def test_the_operator_path_is_not_governed_by_the_scope_flag():
    """The KEEP path must not have been swept up in the gating."""
    from app.core.scope import _deny_self_serve_provisioning
    from app.main import app

    for path in ("/api/platform/tenants", "/api/platform/tenants/{tenant_id}/owner-invite"):
        route = next(r for r in app.routes if getattr(r, "path", None) == path)
        assert _deny_self_serve_provisioning not in [d.dependency for d in route.dependencies], (
            f"{path} was gated — operator onboarding is a KEEP path"
        )


# ── D. The operator path is untouched ────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_operator_can_still_create_a_tenant_and_mint_an_owner_claim_link(isolation_ctx):
    from sqlalchemy import select

    from app.core.security import issue_token
    from app.db import get_db
    from app.main import app
    from app.models import Tenant, User
    from app.models.enums import PlatformRole

    client, _ = isolation_ctx
    session_factory = app.dependency_overrides[get_db]

    agen = session_factory()
    db = await agen.__anext__()
    try:
        op = User(telegram_id=770001, platform_role=PlatformRole.super_admin)
        db.add(op)
        await db.commit()
        await db.refresh(op)
        op_headers = {"Authorization": f"Bearer {issue_token(user_id=str(op.id))}"}
    finally:
        await agen.aclose()

    r = await client.post("/api/platform/tenants",
                          json={"name": "Operator Shop", "plan_code": "lite"},
                          headers=op_headers)
    assert r.status_code == 201, r.text
    tenant_id = r.json()["id"]

    agen = session_factory()
    db = await agen.__anext__()
    try:
        t = (await db.execute(select(Tenant).where(Tenant.slug == "operator-shop"))).scalar_one()
        assert t.name == "Operator Shop"
    finally:
        await agen.aclose()

    r2 = await client.post(f"/api/platform/tenants/{tenant_id}/owner-invite", headers=op_headers)
    assert r2.status_code == 201, r2.text
