"""Rolling back the verifier migration must not destroy live invitations.

A verifier cannot be turned back into a credential — that is the point of
storing one. Dropping the column would leave those rows with no usable secret:
the links in people's chats stop resolving and nothing can reissue them, because
nothing can read them.

An empty schema downgrading cleanly proves none of this, which is why these
tests put rows in first.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import invite_codes


@pytest_asyncio.fixture
async def sess(migrated_db):
    maker = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with maker() as s:
        yield s


#: The revision this suite is about, and the one below it.
RD0 = "rd0invverifier"
BELOW_RD0 = "rc0verifiedid"


def _downgrade_to_rd0() -> None:
    """Step back to rd0 itself, so the rd0 downgrade is what runs next.

    Named rather than `-1`. When this suite was written rd0 WAS head, so `-1`
    happened to mean "undo rd0"; once a later migration landed, the same call
    silently undid that one instead and the suite asserted nothing about the
    behaviour it exists to protect.
    """
    from alembic.config import Config

    from alembic import command

    command.downgrade(Config("alembic.ini"), RD0)


def _downgrade_one() -> None:
    """Undo rd0, run the way the operator runs it."""
    from alembic.config import Config

    from alembic import command

    command.downgrade(Config("alembic.ini"), BELOW_RD0)


def _upgrade_head() -> None:
    from alembic.config import Config

    from alembic import command

    command.upgrade(Config("alembic.ini"), "head")


async def _current_head(engine) -> str:
    """Read the stamped revision on a connection that is closed immediately.

    Deliberately the engine and not the long-lived `sess`. SQLAlchemy autobegins
    a transaction on the first statement, so reading through the session leaves
    it idle-in-transaction for the rest of the test — and an idle transaction on
    this database holds a lock that `CREATE INDEX CONCURRENTLY` waits behind.
    `_upgrade_head()` below replays `rh1paytenantidx`, which builds its index
    that way, so the session would block its own migration until `lock_timeout`
    cancelled it. Borrow a connection, read, give it back.
    """
    async with engine.connect() as conn:
        return (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()


async def _tenant_id(sess) -> uuid.UUID:
    tid = uuid.uuid4()
    # Through the ORM, so every NOT NULL the schema grew is satisfied by its
    # own default rather than re-listed here.
    from app.models import Tenant
    sess.add(Tenant(id=tid, name="Shop", slug=f"s-{uuid.uuid4().hex[:8]}"))
    await sess.flush()
    return tid


async def _add_invitation(sess, *, token=None, verifier=None) -> None:
    await sess.execute(text(
        "INSERT INTO invitations (id, token, token_verifier, tenant_id, kind, preset, "
        "expires_at, created_at) "
        "VALUES (:i, :t, :v, :tid, 'customer', '{}'::jsonb, :e, now())"
    ), {
        "i": uuid.uuid4(), "t": token, "v": verifier,
        "tid": await _tenant_id(sess),
        "e": datetime.now(UTC) + timedelta(days=7),
    })


class TestDowngrade:
    async def test_an_empty_database_downgrades(self, sess, migrated_db):
        import asyncio
        await sess.commit()
        await asyncio.to_thread(_downgrade_to_rd0)
        assert await _current_head(migrated_db) == RD0
        await asyncio.to_thread(_downgrade_one)
        assert await _current_head(migrated_db) == "rc0verifiedid"
        await asyncio.to_thread(_upgrade_head)

    async def test_legacy_plaintext_only_downgrades(self, sess, migrated_db):
        """Those rows keep their credential, so nothing is lost."""
        import asyncio
        await _add_invitation(sess, token="A7K3QX")
        await sess.commit()
        await asyncio.to_thread(_downgrade_to_rd0)
        await asyncio.to_thread(_downgrade_one)
        async with migrated_db.connect() as conn:
            head = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
            kept = (await conn.execute(text("SELECT token FROM invitations"))).scalar_one()
        assert head == "rc0verifiedid"
        assert kept == "A7K3QX"
        await asyncio.to_thread(_upgrade_head)

    async def test_a_verifier_only_row_refuses_the_downgrade(self, sess, migrated_db):
        """Fail closed. The alternative is silently destroying live links."""
        import asyncio
        raw = invite_codes.new_credential()
        await _add_invitation(sess, verifier=invite_codes.verifier(raw))
        await sess.commit()
        await asyncio.to_thread(_downgrade_to_rd0)

        with pytest.raises(RuntimeError) as ei:
            await asyncio.to_thread(_downgrade_one)
        message = str(ei.value)
        assert "cannot be reconstructed" in message
        assert "Nothing has been changed" in message
        # The credential itself must not appear in the refusal.
        assert raw not in message

        async with migrated_db.connect() as conn:
            head = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
            still = (await conn.execute(text(
                "SELECT token_verifier FROM invitations"))).scalar_one()
        assert head == RD0, "the database must stay where it was"
        assert still == invite_codes.verifier(raw), "the verifier must survive"

    async def test_the_refusal_counts_live_rows_separately(self, sess, migrated_db):
        import asyncio
        await _add_invitation(sess, verifier=invite_codes.verifier(invite_codes.new_credential()))
        await sess.execute(text(
            "UPDATE invitations SET expires_at = now() - interval '1 day'"))
        await _add_invitation(sess, verifier=invite_codes.verifier(invite_codes.new_credential()))
        await sess.commit()
        await asyncio.to_thread(_downgrade_to_rd0)
        with pytest.raises(RuntimeError) as ei:
            await asyncio.to_thread(_downgrade_one)
        # Two rows exist; one of them is still live.
        assert "2 invitation(s) (1 still live)" in str(ei.value)

    def test_the_downgrade_never_invents_or_deletes(self):
        """It may refuse. It may not paper over the problem."""
        import pathlib
        src = (pathlib.Path(__file__).resolve().parents[1]
               / "alembic/versions/rd0_invitation_verifier.py").read_text()
        body = src.split("def downgrade")[1]
        for forbidden in ("DELETE", "UPDATE invitations SET token",
                          "new_credential", "token = "):
            assert forbidden not in body, forbidden
        assert "raise RuntimeError" in body
