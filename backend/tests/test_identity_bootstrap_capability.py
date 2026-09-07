"""The pre-authentication authority cannot read or write the user directory.

Before this correction ``muddato_identity`` held ``USING (true) WITH CHECK
(true)`` on ``users`` and the authority was attached to the entire ``/auth``
router — so every handler under it, present and future, ran with read/write
access to every account before a credential had been checked.

It now has NO policy on ``users``. These tests prove that against a real
database through the restricted runtime login, and then prove the legitimate
bootstrap operations still work through the narrow capability functions —
because a directory you have closed by breaking login is not closed, it is
broken.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.rls_manifest import ROLE_APP, ROLE_IDENTITY
from tests import _dbcreds

pytestmark = pytest.mark.asyncio(loop_scope="module")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def engine(rls_world):
    url = _dbcreds.runtime_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — the identity tier needs real Postgres")
    eng = create_async_engine(url, poolclass=None)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _as_identity(engine, sql: str, params: dict | None = None):
    """Run ``sql`` under the pre-auth authority, in a rolled-back transaction.

    Returns ``(rows, rowcount, error)``. All three matter, because row security
    refuses reads and writes DIFFERENTLY and a test that only looks for an
    exception will pass for the wrong reason:

    * SELECT, UPDATE and DELETE are FILTERED by ``USING`` — no matching policy
      means no rows qualify, so they succeed and affect nothing. Silent.
    * INSERT (and the new row of an UPDATE) is checked by ``WITH CHECK`` — no
      matching policy means the row is REJECTED with an error. Loud.

    Both outcomes are "denied". Only "rows came back" or "rows were changed" is
    a failure.
    """
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            await conn.execute(text(f"SET LOCAL ROLE {ROLE_IDENTITY}"))
            await conn.execute(text("SELECT set_config('app.user_id', '', true)"))
            await conn.execute(text("SELECT set_config('app.tenant_id', '', true)"))
            result = await conn.execute(text(sql), params or {})
            rows = result.all() if result.returns_rows else []
            return rows, result.rowcount, None
        except Exception as exc:
            return None, -1, f"{type(exc).__name__}: {exc}".lower()
        finally:
            await trans.rollback()


# ── what the pre-auth authority CANNOT do ────────────────────────────────────

async def test_identity_role_has_no_policy_on_users(engine):
    """Structural: with row security on and no matching policy, everything denies."""
    async with engine.connect() as engine_conn:
        rows = (await engine_conn.execute(text(
            "SELECT policyname FROM pg_policies "
            " WHERE schemaname = 'public' AND tablename = 'users' "
            "   AND :role = ANY (roles)",
        ), {"role": ROLE_IDENTITY})).all()
        (rls_on,) = (await engine_conn.execute(text(
            "SELECT c.relrowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = 'users'",
        ))).first()
    assert rls_on is True, "row security must be ON for the absence of a policy to deny"
    assert rows == [], f"the pre-auth authority still has users policies: {rows}"


async def test_identity_role_cannot_list_users(engine):
    rows, _n, err = await _as_identity(engine, "SELECT id FROM users LIMIT 5")
    assert err is None, f"unexpected error shape: {err}"
    assert rows == [], "the pre-authentication authority listed the user directory"


async def test_identity_role_cannot_select_an_arbitrary_user_uuid(engine, rls_world):
    rows, _n, err = await _as_identity(
        engine, "SELECT id FROM users WHERE id = :u", {"u": rls_world.owner_b},
    )
    assert err is None
    assert rows == []


async def test_identity_role_cannot_enumerate_by_telegram_id(engine, rls_world):
    """A neighbouring identity must not be discoverable by walking ids."""
    rows, _n, err = await _as_identity(
        engine,
        "SELECT id FROM users WHERE telegram_id BETWEEN :lo AND :hi",
        {"lo": rls_world.tg_customer - 50, "hi": rls_world.tg_customer + 50},
    )
    assert err is None
    assert rows == []


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE users SET first_name = 'hijacked' WHERE true",
        "UPDATE users SET platform_role = 'super_admin' WHERE true",
        "UPDATE users SET verified_phone = '+998900000009' WHERE true",
        "UPDATE users SET phone_eligibility = 'ELIGIBLE' WHERE true",
        "DELETE FROM users WHERE true",
        "INSERT INTO users (id, telegram_id, locale, is_active) "
        "VALUES (gen_random_uuid(), 424242, 'uz', true)",
    ],
)
async def test_identity_role_cannot_write_the_directory(engine, statement):
    """Every write shape is denied, including the privilege-escalating ones.

    "Denied" has two faces here and both are asserted, because assuming only one
    would let the other pass silently. An INSERT is rejected by ``WITH CHECK``
    and RAISES. An UPDATE or DELETE is filtered by ``USING``: no row qualifies,
    so it succeeds and changes NOTHING. A test that only looked for an exception
    would report the UPDATE cases as failures; one that only looked at rowcount
    would never notice a policy that started matching.
    """
    _rows, changed, err = await _as_identity(engine, statement)
    if err is not None:
        assert "row-level security" in err or "permission denied" in err, (
            f"denied, but not by an authorization rule: {err[:160]}"
        )
        return
    assert changed == 0, (
        f"the pre-auth authority changed {changed} row(s) with: {statement}"
    )


# ── what the narrow capabilities DO allow ────────────────────────────────────

async def test_valid_telegram_id_resolves_or_creates_exactly_one_account(engine):
    from app.config import settings as app_settings
    from app.core.identity_bootstrap import upsert_telegram_account
    from app.db import reset_db_scope

    Session = async_sessionmaker(engine, expire_on_commit=False)
    was, app_settings.rls_enabled = app_settings.rls_enabled, True
    try:
        reset_db_scope()
        async with Session() as s:
            first = await upsert_telegram_account(
                s, telegram_id=880001, first_name="Aa", locale="ru",
            )
            second = await upsert_telegram_account(
                s, telegram_id=880001, first_name="Bb",
            )
            await s.rollback()
        assert first is not None
        assert first == second, "a returning login must resolve the SAME account"
    finally:
        app_settings.rls_enabled = was
        reset_db_scope()


async def test_bootstrap_cannot_set_a_privileged_field(engine):
    """Mass assignment is impossible by SIGNATURE, not by discipline.

    The SQL function takes six Telegram display fields. There is no parameter
    for ``platform_role``, ``is_active``, ``verified_phone`` or
    ``phone_eligibility``, so no request body shape can reach them through this
    path — which is a stronger statement than "the handler does not pass them".
    """
    async with engine.connect() as conn:
        (args,) = (await conn.execute(text(
            "SELECT pg_catalog.oidvectortypes(p.proargtypes) FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname='public' AND p.proname='rls_identity_upsert_telegram'",
        ))).first()
        (names,) = (await conn.execute(text(
            "SELECT p.proargnames FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname='public' AND p.proname='rls_identity_upsert_telegram'",
        ))).first()
    assert args == "bigint, text, text, text, text, text"
    forbidden = {
        "p_platform_role", "p_is_active", "p_verified_phone",
        "p_phone_eligibility", "p_tokens_valid_from", "p_ref_code",
    }
    assert not (set(names or []) & forbidden), f"privileged parameter present: {names}"


async def test_concurrent_first_login_creates_at_most_one_account(engine):
    """Two simultaneous first logins arbitrate on the unique index, not in Python."""
    import asyncio

    from app.config import settings as app_settings
    from app.core.identity_bootstrap import upsert_telegram_account
    from app.db import reset_db_scope

    Session = async_sessionmaker(engine, expire_on_commit=False)
    was, app_settings.rls_enabled = app_settings.rls_enabled, True
    tg = 880777

    async def _login():
        reset_db_scope()
        async with Session() as s:
            uid = await upsert_telegram_account(s, telegram_id=tg, first_name="Race")
            await s.commit()
            return uid

    try:
        got = await asyncio.gather(_login(), _login(), _login())
        assert len({str(g) for g in got}) == 1, f"first login raced into {got}"
    finally:
        # Disposed explicitly: an undisposed engine leaves a pooled asyncpg
        # connection bound to this module's loop, and its finaliser fires after
        # that loop closes — surfacing as "Event loop is closed" inside whatever
        # module happens to run next.
        cleanup = create_async_engine(_dbcreds.migration_url(), poolclass=None)
        try:
            async with cleanup.begin() as conn:
                await conn.execute(
                    text("DELETE FROM users WHERE telegram_id = :t"), {"t": tg},
                )
        finally:
            await cleanup.dispose()
        app_settings.rls_enabled = was
        reset_db_scope()


async def test_identity_state_answers_only_for_the_id_it_is_given(engine, rls_world):
    from app.config import settings as app_settings
    from app.core.identity_bootstrap import identity_state
    from app.db import reset_db_scope

    Session = async_sessionmaker(engine, expire_on_commit=False)
    was, app_settings.rls_enabled = app_settings.rls_enabled, True
    try:
        reset_db_scope()
        async with Session() as s:
            known = await identity_state(s, rls_world.tg_customer)
            unknown = await identity_state(s, 999_000_111)
        assert known is not None and known.verified is True
        assert unknown is None, "an unknown Telegram id must resolve to nothing"
    finally:
        app_settings.rls_enabled = was
        reset_db_scope()


async def test_capability_functions_are_not_callable_by_ordinary_authorities(engine):
    """A tenant handler must not be able to mint or inspect accounts."""
    for role in (ROLE_APP, "muddato_customer", "muddato_worker"):
        with pytest.raises(Exception) as caught:
            async with engine.begin() as conn:
                await conn.execute(text(f"SET LOCAL ROLE {role}"))
                await conn.execute(text(
                    "SELECT public.rls_identity_upsert_telegram(1, NULL, NULL, "
                    "NULL, NULL, NULL)",
                ))
        assert "permission denied" in str(caught.value).lower(), (
            f"{role} can mint accounts through the identity capability"
        )


async def test_public_holds_no_execute_on_any_capability(engine):
    """Asked on the OWNER connection, because the question is about the
    CATALOGUE rather than about what any one role can do.

    ``has_function_privilege`` has to resolve the function name, and resolving a
    qualified name needs USAGE on its schema — which the runtime login has, but
    which a cluster whose ``public`` schema was created by hand may not grant.
    Asking as the owner makes the check independent of that, and the runtime
    login's own posture (USAGE granted, CREATE denied, no direct table or
    sequence privilege) is asserted in ``test_runtime_credential_isolation``."""
    from app.core.rls_manifest import CAPABILITY_FUNCTIONS

    owner = create_async_engine(_dbcreds.migration_url(), poolclass=None)
    async with owner.connect() as conn:
        for signature in CAPABILITY_FUNCTIONS:
            (public_exec,) = (await conn.execute(
                text("SELECT has_function_privilege('public', :fn, 'EXECUTE')"),
                {"fn": f"public.{signature}"},
            )).first()
            assert public_exec is False, f"PUBLIC can execute {signature}"
    await owner.dispose()


async def test_every_capability_pins_its_search_path(engine):
    """An unpinned ``search_path`` in a definer function is a takeover primitive."""
    from app.core.rls_manifest import CAPABILITY_FUNCTIONS

    owner = create_async_engine(_dbcreds.migration_url(), poolclass=None)
    async with owner.connect() as conn:
        rows = {
            f"{name}({args})": list(config or [])
            for name, args, config in (await conn.execute(text(
                "SELECT p.proname, pg_catalog.oidvectortypes(p.proargtypes), p.proconfig "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public' AND p.prosecdef",
            ))).all()
        }
    await owner.dispose()
    for signature in CAPABILITY_FUNCTIONS:
        config = rows.get(signature)
        assert config is not None, f"{signature} is missing or not SECURITY DEFINER"
        assert any(c.startswith("search_path=") for c in config), (
            f"{signature} does not pin search_path"
        )


async def test_auth_router_carries_no_blanket_authority():
    """No handler under ``/auth`` may inherit an authority from its router.

    This is the structural half of the fix: even with the directory policy gone,
    re-attaching a router-level grant would put every future ``/auth`` endpoint
    back inside whatever that authority can reach.
    """
    from fastapi.routing import APIRoute

    from app.main import app

    offenders = []
    checked = 0
    for route in app.routes:
        if not isinstance(route, APIRoute) or "/auth" not in route.path:
            continue
        checked += 1
        names = {
            getattr(d.call, "__name__", "") for d in route.dependant.dependencies
            if d.call is not None
        }
        if names & {"identity_authority", "_identity_authority", "worker_authority"}:
            offenders.append(route.path)
    assert checked >= 3
    assert offenders == [], f"/auth routes with a blanket authority: {offenders}"


def test_identity_authority_is_not_importable_as_a_router_dependency():
    """The name is gone from ``dbauth`` on purpose — re-adding it is a decision."""
    from app.core import dbauth

    assert not hasattr(dbauth, "identity_authority")
    assert "identity_authority" not in dbauth.__all__


# ── the capability context manager's own lifecycle ───────────────────────────

async def test_a_failing_capability_body_reports_its_own_error(engine):
    """The reason a capability call failed must survive the restore.

    ``capability()`` puts the previous authority back in a ``finally``, and
    doing that means issuing SQL. If the body has just failed, the transaction
    is aborted and that restore statement fails too — with
    ``InFailedSqlTransaction``, which then REPLACES the real error. An operator
    sees "current transaction is aborted" instead of "permission denied for
    table users", which is the difference between a five-minute diagnosis and an
    hour of looking in the wrong place.

    The authority itself is not at risk either way: the context variable is
    restored before any statement is attempted, and ``after_begin`` re-applies
    it on the next transaction. This is about the error being legible.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.config import settings as app_settings
    from app.db import DbAuthority, capability, current_db_scope, reset_db_scope

    Session = async_sessionmaker(engine, expire_on_commit=False)
    was, app_settings.rls_enabled = app_settings.rls_enabled, True
    try:
        reset_db_scope()
        async with Session() as session:
            # Deliberately broad: the point is WHICH error surfaces, and
            # narrowing the type here would pass on the wrong one.
            with pytest.raises(Exception) as caught:
                async with capability(session, DbAuthority.webhook):
                    # muddato_webhook holds no table privilege at all, so this
                    # is the ordinary refusal a capability body can hit.
                    await session.execute(text("SELECT 1 FROM users LIMIT 1"))
        message = str(caught.value).lower()
        assert "permission denied" in message, (
            f"the body's error was replaced by the restore's: {message[:160]}"
        )
        assert "transaction is aborted" not in message
        # …and the authority is back regardless.
        assert current_db_scope()["authority"] == DbAuthority.app.value
    finally:
        app_settings.rls_enabled = was
        reset_db_scope()


async def test_a_successful_capability_still_surfaces_a_restore_failure(engine):
    """The other half: silence is only for the already-failing case.

    Swallowing restore errors unconditionally would hide a genuine inability to
    put the authority back — which, on a healthy transaction, is a real fault
    and must not be quiet.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app import db as dbmod
    from app.config import settings as app_settings
    from app.db import DbAuthority, capability, reset_db_scope

    Session = async_sessionmaker(engine, expire_on_commit=False)
    was, app_settings.rls_enabled = app_settings.rls_enabled, True
    calls = {"n": 0}
    original = dbmod.apply_db_scope

    async def _fail_on_restore(session):
        calls["n"] += 1
        if calls["n"] > 1:          # the entry call succeeds, the restore does not
            raise RuntimeError("restore failed")
        await original(session)

    try:
        reset_db_scope()
        dbmod.apply_db_scope = _fail_on_restore
        async with Session() as session:
            with pytest.raises(RuntimeError, match="restore failed"):
                async with capability(session, DbAuthority.webhook):
                    pass            # the body SUCCEEDS
    finally:
        dbmod.apply_db_scope = original
        app_settings.rls_enabled = was
        reset_db_scope()
