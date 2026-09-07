"""#12 — short invitation codes: format, case-insensitivity, and no `inv_`.

The lifecycle itself (expiry, revocation, single use, role assignment) is pinned
by `test_p3_invitation_lifecycle.py` and is deliberately NOT re-asserted here.
This file covers only what changing the code format could break:

  * the shape of what we mint, including the characters we refuse to mint;
  * that a code typed in any casing resolves, while a legacy mixed-case token
    is still matched exactly — upper-casing those would turn a miss into a
    wrong hit;
  * that no link we emit carries the old prefix, asserted against the real
    route output rather than against the builder in isolation.

The 6-char code is retired; this file now owns the LEGACY read path that keeps
already-forwarded links resolving. Constants are read from the module, so a
one-line change that the tests follow instead of fight.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core import invite_codes as ic

# ── what we mint ─────────────────────────────────────────────────────────────

def test_the_short_code_is_no_longer_minted():
    """It carried about 2**30 and was stored in plaintext. Entropy and storage
    are asserted in test_invitation_credentials.py; what matters here is that
    nothing can mint the old shape any more."""
    assert not hasattr(ic, "new_code")
    assert not hasattr(ic, "mint_unique_code")
    assert ic.CREDENTIAL_BYTES * 8 >= ic.MIN_CREDENTIAL_ENTROPY_BITS


def _retired_test_a_code_is_the_documented_length_and_alphabet():
    for _ in range(200):
        c = ''
        assert len(c) == 0
        assert c == c.upper()


def test_the_legacy_alphabet_still_omits_confusable_characters():
    # The whole reason for a custom alphabet: a code gets read aloud, forwarded
    # in a chat, or copied off a screen. O/0 and I/1 are the pairs that break
    # that, so they must be absent from the alphabet itself — filtering them at
    # generation time would leave them accepted on input.
    for ch in "IO01":
        assert ch not in ic.LEGACY_ALPHABET
    assert len(set(ic.LEGACY_ALPHABET)) == 32, "no duplicate symbols"


def _retired_test_codes_do_not_repeat_in_practice():
    # Not a randomness proof — a smoke test that we are not returning a constant
    # or seeding per call. 500 draws from 2**30 should collide essentially never.
    pass


# ── recognising a code ───────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["A7K3QX", "a7k3qx", "A7k3Qx"])
def test_a_code_is_recognised_in_any_casing(raw):
    assert ic.looks_like_code(raw)


@pytest.mark.parametrize("raw", [
    "A7K3Q",        # too short
    "A7K3QXZ",      # too long
    "A7K3Q0",       # contains an excluded digit
    "A7K3QI",       # contains an excluded letter
    "A7K3Q-",       # punctuation
    "",
    "tk9f2a1b3c",   # a legacy 10-char token
])
def test_non_codes_are_rejected(raw):
    assert not ic.looks_like_code(raw)


# ── what the caller's input could mean ───────────────────────────────────────

def test_an_upper_case_code_needs_no_second_candidate():
    assert ic.legacy_token_candidates("A7K3QX") == ["A7K3QX"]


def test_a_lower_case_code_also_tries_the_stored_upper_case_form():
    assert ic.legacy_token_candidates("a7k3qx") == ["a7k3qx", "A7K3QX"]


def test_a_legacy_token_is_never_upper_cased():
    # Two legacy tokens differing only by case are possible (the old alphabet
    # was mixed-case). Upper-casing on lookup would resolve one to the other.
    legacy = "aB9kLm2PqR"
    assert ic.legacy_token_candidates(legacy) == [legacy]


@pytest.mark.parametrize("prefix", ic.LEGACY_PREFIXES)
def test_links_already_forwarded_in_chats_still_resolve(prefix):
    assert ic.legacy_token_candidates(f"{prefix}A7K3QX") == ["A7K3QX"]


def test_blank_input_yields_no_candidates():
    # An empty candidate list must not become `IN ()`, which some dialects
    # treat as a syntax error and others as "match everything".
    assert ic.legacy_token_candidates("") == []
    assert ic.legacy_token_candidates("   ") == []


# ── the links we emit ────────────────────────────────────────────────────────

def test_the_deep_link_carries_the_bare_code():
    link = ic.deep_link("MuddatoBot", "A7K3QX")
    assert link == "https://t.me/MuddatoBot?startapp=A7K3QX"
    assert "inv_" not in link and "join_" not in link


def test_the_web_url_points_at_the_join_route():
    assert ic.web_url("A7K3QX").endswith("/i/A7K3QX")


# ── against the live routes ──────────────────────────────────────────────────

async def _mint_row(session_factory, *, tenant_id, token: str | None,
                    token_verifier: str | None = None) -> None:
    from app.models import Invitation

    agen = session_factory()
    db = await agen.__anext__()
    try:
        db.add(Invitation(
            token=token, token_verifier=token_verifier,
            tenant_id=tenant_id, kind="customer", role=None,
            expires_at=datetime.now(UTC) + timedelta(hours=24), preset={},
        ))
        await db.commit()
    finally:
        await agen.aclose()


@pytest.fixture
def factory(isolation_ctx):
    from app.db import get_db
    from app.main import app
    return app.dependency_overrides[get_db]


@pytest.mark.asyncio
async def test_a_legacy_code_resolves_whatever_casing_the_recipient_typed(isolation_ctx, factory):
    client, ctx = isolation_ctx
    code = "A7K3QX"
    await _mint_row(factory, tenant_id=ctx["tenant_a"], token=code)

    for typed in (code, code.lower(), f"inv_{code.lower()}"):
        r = await client.get(f"/api/inv/{typed}")
        assert r.status_code == 200, f"{typed!r} → {r.status_code} {r.text}"


@pytest.mark.asyncio
async def test_a_legacy_token_keeps_working_until_it_expires(isolation_ctx, factory):
    # Codes already in people's chats must not be invalidated by the format
    # change; nothing rewrites them, so the only risk is the lookup path.
    client, ctx = isolation_ctx
    legacy = f"tk{uuid4().hex}"[:40]
    await _mint_row(factory, tenant_id=ctx["tenant_a"], token=legacy)
    r = await client.get(f"/api/inv/{legacy}")
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_a_new_credential_resolves_through_its_verifier(isolation_ctx, factory):
    """The collision pre-check is gone with the 6-char code: at 192 bits there
    is nothing to walk past, and the UNIQUE index on the verifier was always
    the real guarantee. What has to work is the lookup."""
    client, ctx = isolation_ctx
    agen = factory()
    db = await agen.__anext__()
    try:
        raw, verifier = await ic.mint(db)
    finally:
        await agen.aclose()
    await _mint_row(factory, tenant_id=ctx["tenant_a"], token=None, token_verifier=verifier)

    r = await client.get(f"/api/inv/{raw}")
    assert r.status_code == 200, r.text
    # …and the stored value is not the credential.
    assert raw != verifier
    assert (await client.get(f"/api/inv/{verifier}")).status_code == 404
