"""An invitation is a bearer credential, so it has to behave like one.

The retired form was six characters — about 2**30 — kept in plaintext. Rate
limiting made the ONLINE guess impractical and did nothing about a database
dump, which handed over every live link. Both halves are fixed here: 192 bits of
entropy, and only a verifier at rest.

Hashing without the entropy would be theatre: SHA-256 of a six-character code is
a rainbow table. Both properties are asserted, together.
"""
from __future__ import annotations

import math
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import invite_codes
from app.models import Invitation, Tenant


@pytest_asyncio.fixture
async def sess(migrated_db):
    maker = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with maker() as s:
        yield s


class TestCredentialStrength:
    def test_a_credential_carries_at_least_128_bits(self):
        raw = invite_codes.new_credential()
        # url-safe base64: 6 bits per character, and the generator is `secrets`.
        bits = invite_codes.CREDENTIAL_BYTES * 8
        assert bits >= invite_codes.MIN_CREDENTIAL_ENTROPY_BITS
        assert bits >= 128
        assert len(raw) >= math.ceil(bits / 6)

    def test_credentials_do_not_repeat(self):
        seen = {invite_codes.new_credential() for _ in range(500)}
        assert len(seen) == 500

    def test_a_credential_is_url_and_startapp_safe(self):
        """Telegram's `startapp` accepts [A-Za-z0-9_-] up to 64 characters, and
        the browser link puts the same value in a path segment."""
        for _ in range(50):
            raw = invite_codes.new_credential()
            assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", raw), raw
            assert invite_codes.deep_link("MuddatoBot", raw).endswith(raw)
            assert invite_codes.web_url(raw).endswith(f"/i/{raw}")

    def test_the_verifier_is_not_the_credential(self):
        raw = invite_codes.new_credential()
        v = invite_codes.verifier(raw)
        assert v != raw
        assert len(v) == 64 and re.fullmatch(r"[0-9a-f]{64}", v)

    def test_the_verifier_is_domain_separated_from_a_bare_digest(self):
        """A plain SHA-256 would be interchangeable with every other digest the
        application computes, and guessable without the key."""
        import hashlib
        raw = invite_codes.new_credential()
        assert invite_codes.verifier(raw) != hashlib.sha256(raw.encode()).hexdigest()

    def test_the_signing_key_is_not_the_bot_token(self):
        """The bot token is shared with Telegram and rotates for unrelated
        reasons; an invitation must not become forgeable because it did."""
        import pathlib
        src = (pathlib.Path(invite_codes.__file__)).read_text()
        assert "telegram_bot_token" not in src
        assert "settings.secret_key" in src

    def test_different_credentials_give_different_verifiers(self):
        a, b = invite_codes.new_credential(), invite_codes.new_credential()
        assert invite_codes.verifier(a) != invite_codes.verifier(b)

    def test_the_module_no_longer_mints_the_short_code(self):
        import pathlib
        src = (pathlib.Path(invite_codes.__file__)).read_text()
        assert "def new_code" not in src
        assert "mint_unique_code" not in src


class TestStorage:
    async def _tenant(self, sess) -> Tenant:
        t = Tenant(name="Shop", slug=f"s-{uuid.uuid4().hex[:8]}")
        sess.add(t)
        await sess.flush()
        return t

    async def test_the_row_does_not_contain_the_credential(self, sess):
        """The property a dump depends on."""
        t = await self._tenant(sess)
        raw, v = await invite_codes.mint(sess)
        inv = Invitation(token_verifier=v, tenant_id=t.id, kind="customer", preset={},
                         expires_at=datetime.now(UTC) + timedelta(days=1))
        sess.add(inv)
        await sess.commit()

        row = (await sess.execute(text(
            "SELECT token, token_verifier FROM invitations WHERE id = :i"
        ), {"i": inv.id})).one()
        assert row.token is None
        assert row.token_verifier == v
        assert raw not in (row.token_verifier or "")

    async def test_the_credential_resolves_through_its_verifier(self, sess):
        t = await self._tenant(sess)
        raw, v = await invite_codes.mint(sess)
        sess.add(Invitation(token_verifier=v, tenant_id=t.id, kind="customer", preset={},
                            expires_at=datetime.now(UTC) + timedelta(days=1)))
        await sess.commit()
        found = (await sess.execute(
            select(Invitation).where(invite_codes.lookup_filter(raw))
        )).scalar_one_or_none()
        assert found is not None

    async def test_a_wrong_credential_resolves_to_nothing(self, sess):
        t = await self._tenant(sess)
        _raw, v = await invite_codes.mint(sess)
        sess.add(Invitation(token_verifier=v, tenant_id=t.id, kind="customer", preset={},
                            expires_at=datetime.now(UTC) + timedelta(days=1)))
        await sess.commit()
        for wrong in (invite_codes.new_credential(), "", "   ", "ABC123", v):
            found = (await sess.execute(
                select(Invitation).where(invite_codes.lookup_filter(wrong))
            )).scalar_one_or_none()
            assert found is None, wrong

    async def test_an_empty_input_does_not_match_every_new_row(self, sess):
        """`WHERE token IS NULL` would match every verifier-only invitation."""
        t = await self._tenant(sess)
        for _ in range(3):
            _raw, v = await invite_codes.mint(sess)
            sess.add(Invitation(token_verifier=v, tenant_id=t.id, kind="customer", preset={},
                                expires_at=datetime.now(UTC) + timedelta(days=1)))
        await sess.commit()
        rows = (await sess.execute(
            select(Invitation).where(invite_codes.lookup_filter(""))
        )).scalars().all()
        assert rows == []

    async def test_a_legacy_plaintext_link_still_works_until_it_expires(self, sess):
        t = await self._tenant(sess)
        sess.add(Invitation(token="A7K3QX", tenant_id=t.id, kind="customer", preset={},
                            expires_at=datetime.now(UTC) + timedelta(days=1)))
        await sess.commit()
        for spelling in ("A7K3QX", "a7k3qx", "inv_A7K3QX"):
            found = (await sess.execute(
                select(Invitation).where(invite_codes.lookup_filter(spelling))
            )).scalar_one_or_none()
            assert found is not None, spelling

    async def test_a_row_carries_exactly_one_credential(self, sess):
        """Neither both nor neither — the database refuses the other shapes."""
        from sqlalchemy.exc import IntegrityError
        t = await self._tenant(sess)
        for token, verifier in ((None, None), ("A7K3QX", "deadbeef" * 8)):
            sess.add(Invitation(token=token, token_verifier=verifier, tenant_id=t.id,
                                kind="customer", preset={},
                                expires_at=datetime.now(UTC) + timedelta(days=1)))
            with pytest.raises(IntegrityError):
                await sess.flush()
            await sess.rollback()

    async def test_two_rows_cannot_share_a_verifier(self, sess):
        from sqlalchemy.exc import IntegrityError
        t = await self._tenant(sess)
        _raw, v = await invite_codes.mint(sess)
        for _ in range(2):
            sess.add(Invitation(token_verifier=v, tenant_id=t.id, kind="customer", preset={},
                                expires_at=datetime.now(UTC) + timedelta(days=1)))
        with pytest.raises(IntegrityError):
            await sess.flush()
        await sess.rollback()


class TestOpaqueCredential:
    """A high-entropy credential is a secret, not a formatted identifier.

    `token_urlsafe` draws from 64 symbols, so about one credential in a thousand
    legitimately begins `inv_` or `join_`. Stripping that as if it were the
    retired prefix would delete four characters of a 192-bit secret before
    hashing it, and the link would fail for reasons nobody could reproduce.
    """

    @pytest.mark.parametrize("raw", [
        "inv_" + "A" * 28,
        "join_" + "B" * 27,
        "INV_" + "c" * 28,
        "Inv_" + "d" * 28,
        "join_" + "e" * 27,
        "aB3_x-Yz" + "9" * 24,
        "_" + "f" * 31,
        "-" + "g" * 31,
    ])
    def test_a_credential_is_hashed_exactly_as_minted(self, raw):
        assert invite_codes.verifier(raw) == invite_codes.verifier(raw)
        # Nothing was removed, folded or trimmed on the way in.
        assert invite_codes.verifier(raw) != invite_codes.verifier(raw.upper()) or raw == raw.upper()
        assert invite_codes.strip_legacy_prefix(raw) == raw

    def test_only_a_genuine_legacy_code_loses_its_prefix(self):
        assert invite_codes.strip_legacy_prefix("inv_A7K3QX") == "A7K3QX"
        assert invite_codes.strip_legacy_prefix("join_A7K3QX") == "A7K3QX"
        # …and nothing else does, whatever it starts with.
        long_one = "inv_" + "Z" * 28
        assert invite_codes.strip_legacy_prefix(long_one) == long_one

    def test_outer_whitespace_is_the_only_thing_removed(self):
        raw = invite_codes.new_credential()
        assert invite_codes.verifier(f"  {raw}  ") == invite_codes.verifier(raw)

    async def test_case_is_significant(self, sess):
        """The retired code was case-insensitive; a credential is not. Folding
        case would hash a different string and resolve to nothing."""
        t = Tenant(name="Shop", slug=f"s-{uuid.uuid4().hex[:8]}")
        sess.add(t)
        await sess.flush()
        raw = "aBcDeF" + "g" * 26          # mixed case, 32 chars
        sess.add(Invitation(token_verifier=invite_codes.verifier(raw), tenant_id=t.id,
                            kind="customer", preset={},
                            expires_at=datetime.now(UTC) + timedelta(days=1)))
        await sess.commit()
        assert (await sess.execute(
            select(Invitation).where(invite_codes.lookup_filter(raw)))).scalar_one_or_none()
        for wrong in (raw.upper(), raw.lower()):
            assert (await sess.execute(
                select(Invitation).where(invite_codes.lookup_filter(wrong))
            )).scalar_one_or_none() is None, wrong

    async def test_a_prefixed_credential_still_resolves(self, sess):
        """The end-to-end version: mint something that happens to start `inv_`,
        store its verifier, and look it up."""
        t = Tenant(name="Shop", slug=f"s-{uuid.uuid4().hex[:8]}")
        sess.add(t)
        await sess.flush()
        for raw in ("inv_" + "K" * 28, "join_" + "m" * 27, "INV_" + "n" * 28):
            sess.add(Invitation(
                token_verifier=invite_codes.verifier(raw), tenant_id=t.id,
                kind="customer", preset={},
                expires_at=datetime.now(UTC) + timedelta(days=1),
            ))
            await sess.commit()
            found = (await sess.execute(
                select(Invitation).where(invite_codes.lookup_filter(raw))
            )).scalar_one_or_none()
            assert found is not None, raw

    async def test_a_prefixed_credential_is_not_confused_with_a_legacy_code(self, sess):
        """Both populations coexist and neither answers for the other."""
        t = Tenant(name="Shop", slug=f"s-{uuid.uuid4().hex[:8]}")
        sess.add(t)
        await sess.flush()
        sess.add(Invitation(token="A7K3QX", tenant_id=t.id, kind="customer", preset={},
                            expires_at=datetime.now(UTC) + timedelta(days=1)))
        modern = "inv_" + "P" * 28
        sess.add(Invitation(token_verifier=invite_codes.verifier(modern), tenant_id=t.id,
                            kind="staff", preset={},
                            expires_at=datetime.now(UTC) + timedelta(days=1)))
        await sess.commit()
        legacy_hit = (await sess.execute(
            select(Invitation).where(invite_codes.lookup_filter("inv_A7K3QX")))).scalar_one()
        modern_hit = (await sess.execute(
            select(Invitation).where(invite_codes.lookup_filter(modern)))).scalar_one()
        assert legacy_hit.kind == "customer"
        assert modern_hit.kind == "staff"


class TestNoPathLeaksIt:
    def test_every_mint_uses_the_shared_implementation(self):
        """One definition, or the weakest call site is the real one."""
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1] / "app"
        minting = [p for p in root.rglob("*.py") if "Invitation(" in p.read_text()]
        assert minting, "no mint sites found — the search is wrong"
        for path in minting:
            src = path.read_text()
            if "token_verifier=" not in src:
                continue
            # Nothing constructs an Invitation with a plaintext token any more.
            assert not re.search(r"Invitation\((?:[^()]|\([^()]*\))*\btoken=", src, re.S), path

    def test_no_source_logs_a_raw_credential(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1] / "app"
        for path in root.rglob("*.py"):
            for call in re.findall(r"logger\.\w+\((?:[^()]|\([^()]*\))*\)", path.read_text(), re.S):
                low = call.lower()
                if "token" in low and "verifier" not in low and "bot" not in low:
                    assert "invit" not in low, f"{path}: {call}"

    def test_the_list_endpoint_cannot_return_a_stored_credential(self):
        """There is nothing to return: storage holds a verifier. The response
        model must therefore allow the links to be absent, and the serializer
        must only fill them from a freshly minted value."""
        import inspect

        from app.api.invitations import InvitationOut, _serialize

        assert InvitationOut.model_fields["token"].default is None
        assert InvitationOut.model_fields["web_url"].default is None
        src = inspect.getsource(_serialize)
        assert "raw" in src
        assert "inv.token" not in src

    async def test_a_minted_credential_is_returned_once_and_then_unrecoverable(self, sess):
        t = await self._tenant(sess) if hasattr(self, "_tenant") else None
        if t is None:
            t = Tenant(name="Shop", slug=f"s-{uuid.uuid4().hex[:8]}")
            sess.add(t)
            await sess.flush()
        raw, v = await invite_codes.mint(sess)
        inv = Invitation(token_verifier=v, tenant_id=t.id, kind="customer", preset={},
                         expires_at=datetime.now(UTC) + timedelta(days=1))
        sess.add(inv)
        await sess.commit()
        # Everything persisted about this invitation, searched for the credential.
        dump = (await sess.execute(text(
            "SELECT row_to_json(i)::text FROM invitations i WHERE i.id = :x"
        ), {"x": inv.id})).scalar_one()
        assert raw not in dump
