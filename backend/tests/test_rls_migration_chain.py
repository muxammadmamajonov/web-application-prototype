"""The migration chain around rf0failclosed: one head, and it goes both ways.

Three properties, each of which has broken a deploy in this repository before:
the chain must have exactly ONE head (a second head means `alembic upgrade head`
picks one and silently skips the other), an empty database must reach the head,
and the new revision must be reversible and re-appliable — because the first
thing anyone does with a security migration that misbehaves is roll it back.

The rollback is safe to *run*; it is not safe to *leave*. ``rf0failclosed``'s
downgrade restores the fail-open policies on purpose, since that is what
reversing it honestly means. This file asserts that the reversal is mechanically
clean AND that it really does reopen the hole, so nobody can mistake it for a
neutral operation.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

#: Only the DB-backed checks are async; the chain-shape checks read the
#: script directory and need no loop at all.
_module_loop = pytest.mark.asyncio(loop_scope="module")

#: The revision this file is ABOUT — the credential split — and the one under it.
#: Deliberately not "the newest revision": ordinary schema migrations land on top
#: of these two all the time, and the security properties asserted below belong to
#: this pair wherever the chain currently ends.
CREDENTIAL_SPLIT = "rg0credsplit"
PREVIOUS = "rf0failclosed"


def _tip() -> str:
    """Wherever the chain currently ends.

    (MUDD-66) This was a hard-coded literal, so EVERY migration — security-related
    or not — failed this file until its author found and bumped the constant. The
    property worth asserting is that there is exactly ONE head, not what it is
    called this week.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert len(heads) == 1, f"expected a single head, found {heads}"
    return heads[0]
#: The last revision BEFORE tenant isolation was made fail-closed. Kept so the
#: rollback claim on ``rf0failclosed`` can still be measured at its own stop.
FAIL_OPEN_BASE = "re2noticeretry"


def _alembic(command_name: str, revision: str) -> None:
    from alembic.config import Config

    from alembic import command

    getattr(command, command_name)(Config("alembic.ini"), revision)


def test_the_chain_has_exactly_one_head() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    heads = script.get_heads()
    assert len(heads) == 1, f"expected a single head, found {heads}"
    # …and the credential split is still ON that chain, not orphaned beside it.
    ancestry = {r.revision for r in script.iterate_revisions(heads[0], "base")}
    assert CREDENTIAL_SPLIT in ancestry


def test_the_new_revision_follows_the_previous_one() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    rev = script.get_revision(CREDENTIAL_SPLIT)
    assert rev.down_revision == PREVIOUS


@_module_loop
async def test_an_empty_database_reaches_the_head(rls_world) -> None:
    """``rls_world`` builds itself by migrating a dropped schema, so reaching
    this test at all is the proof; the stamp is the receipt."""
    async with rls_world.engine.connect() as conn:
        stamped = (await conn.execute(
            text("SELECT version_num FROM alembic_version"))).scalar_one()
        await conn.rollback()
    assert stamped == _tip()


@_module_loop
async def test_downgrade_then_upgrade_restores_the_fail_closed_posture(
    rls_world,
) -> None:
    """Two downgrades, two documented risks, each checked where it lives.

    Both migrations say in their docstring that rolling them back reopens
    something. A claim like that is worth nothing unless it is true, so each is
    measured at its own stop:

      tip  → rf0failclosed   the pre-authentication identity authority gets the
                             whole ``users`` directory back (rg0's stated risk);
                             tenant isolation is UNAFFECTED and must stay closed.
      rf0  → re2noticeretry  the fail-open tenant policies come back (rf0's
                             stated risk): an unscoped session sees both shops.
      back to the tip        closed again, on both counts.
    """
    async def _unscoped(role: str, table: str) -> int:
        async with rls_world.engine.connect() as conn:
            tr = await conn.begin()
            try:
                await conn.execute(text(f"SET LOCAL ROLE {role}"))
                return (await conn.execute(
                    text(f"SELECT count(*) FROM {table}"))).scalar_one()
            finally:
                await tr.rollback()

    assert await _unscoped("muddato_app", "customers") == 0
    assert await _unscoped("muddato_identity", "users") == 0

    try:
        await asyncio.to_thread(_alembic, "downgrade", PREVIOUS)
        async with rls_world.engine.connect() as conn:
            at = (await conn.execute(
                text("SELECT version_num FROM alembic_version"))).scalar_one()
            await conn.rollback()
        assert at == PREVIOUS
        assert await _unscoped("muddato_identity", "users") == 4, (
            "rg0's downgrade claims to restore directory-wide identity access; "
            "it must actually do so, or the rollback risk documented on it is "
            "not the real risk"
        )
        assert await _unscoped("muddato_app", "customers") == 0, (
            "rolling back the credential split must not touch TENANT isolation"
        )

        await asyncio.to_thread(_alembic, "downgrade", FAIL_OPEN_BASE)
        assert await _unscoped("muddato_app", "customers") == 2, (
            "rf0's downgrade claims to restore the r73 fail-open posture; it "
            "must actually do so"
        )
    finally:
        await asyncio.to_thread(_alembic, "upgrade", "head")
    assert await _unscoped("muddato_identity", "users") == 0

    async with rls_world.engine.connect() as conn:
        at = (await conn.execute(
            text("SELECT version_num FROM alembic_version"))).scalar_one()
        await conn.rollback()
    assert at == _tip()
    assert await _unscoped("muddato_app", "customers") == 0


@_module_loop
async def test_reapplying_the_migration_is_idempotent(rls_world) -> None:
    """Roles are cluster-scoped and survive a schema drop, so every role and
    policy statement has to converge rather than fail on second application."""
    await asyncio.to_thread(_alembic, "downgrade", PREVIOUS)
    await asyncio.to_thread(_alembic, "upgrade", "head")
    await asyncio.to_thread(_alembic, "downgrade", PREVIOUS)
    await asyncio.to_thread(_alembic, "upgrade", "head")

    async with rls_world.engine.connect() as conn:
        policies = (await conn.execute(text(
            "SELECT count(*) FROM pg_policies WHERE schemaname = 'public'"
        ))).scalar_one()
        dupes = (await conn.execute(text(
            "SELECT count(*) FROM (SELECT tablename, policyname FROM pg_policies "
            "WHERE schemaname='public' GROUP BY 1,2 HAVING count(*) > 1) d"
        ))).scalar_one()
        await conn.rollback()
    assert dupes == 0, "re-applying the migration duplicated a policy"
    assert policies > 100, "policies went missing on re-application"
