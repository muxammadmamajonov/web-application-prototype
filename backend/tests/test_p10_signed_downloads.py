"""#10 — signed download URLs: the signature is the capability, so it has to hold.

These URLs are handed to a system browser with no session attached, which means
the HMAC is the ONLY thing between a stranger and someone's contract. The tests
below are therefore mostly adversarial: they try to move the expiry, swap the
kind, reuse a signature across objects, and walk past the deadline.

The happy path is one test. The attacks are the rest.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.security import sign_download, verify_download_sig


def _exp(minutes: int = 10) -> int:
    return int((datetime.now(UTC) + timedelta(minutes=minutes)).timestamp())


# ── the happy path ───────────────────────────────────────────────────────────

def test_a_freshly_signed_url_verifies():
    e = _exp()
    assert verify_download_sig("contract", "abc", e, sign_download("contract", "abc", e))


# ── expiry is signed, not merely declared ────────────────────────────────────

def test_an_elapsed_deadline_is_refused_even_though_the_signature_is_genuine():
    e = int((datetime.now(UTC) - timedelta(seconds=1)).timestamp())
    assert not verify_download_sig("contract", "abc", e, sign_download("contract", "abc", e))


def test_moving_the_deadline_forward_invalidates_the_signature():
    # The attack the signed expiry exists to stop: take a real (expired) link and
    # edit `?e=` to a future value. The signature covers the expiry, so it fails.
    past = int((datetime.now(UTC) - timedelta(hours=1)).timestamp())
    sig = sign_download("contract", "abc", past)
    assert not verify_download_sig("contract", "abc", _exp(), sig)


def test_a_missing_expiry_does_not_default_to_valid():
    # The route defaults `e` to 0 when the parameter is absent; 0 is in 1970, and
    # a signature over 0 must not be silently accepted either.
    assert not verify_download_sig("contract", "abc", 0, sign_download("contract", "abc", 0))


# ── keyspace separation ──────────────────────────────────────────────────────

def test_a_signature_for_one_kind_does_not_authorise_another():
    e = _exp()
    sig = sign_download("contract", "abc", e)
    assert not verify_download_sig("invoice", "abc", e, sig)
    assert not verify_download_sig("payment_receipt", "abc", e, sig)


def test_a_signature_for_one_object_does_not_authorise_another():
    e = _exp()
    sig = sign_download("contract", "aaa", e)
    assert not verify_download_sig("contract", "bbb", e, sig)


def test_download_signatures_are_a_separate_keyspace_from_receipts_and_media():
    # A receipt URL is permanent; a download URL expires. If the two keyspaces
    # overlapped, a permanent receipt signature could be replayed as a download
    # capability and would never expire.
    from app.core.security import sign_media, sign_receipt

    e = _exp()
    assert sign_download("receipt", "abc", e) != sign_receipt("abc")
    assert sign_download("media", "abc", e) != sign_media("abc")


# ── malformed input ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("sig", [None, "", "  ", "0" * 32, "deadbeef"])
def test_absent_or_junk_signatures_are_refused(sig):
    assert not verify_download_sig("contract", "abc", _exp(), sig)


def test_the_signature_is_long_enough_to_not_be_guessable():
    # 32 hex chars = 128 bits. Enough that brute force is not the weak link.
    assert len(sign_download("contract", "abc", _exp())) == 32


def test_signing_is_deterministic_for_the_same_inputs():
    # Required for verification to work at all, and it means the URL can be
    # re-derived rather than stored.
    e = _exp()
    assert sign_download("contract", "abc", e) == sign_download("contract", "abc", e)


# ── the minted URL ───────────────────────────────────────────────────────────

def test_the_url_is_absolute_https_because_telegram_will_not_open_a_relative_one():
    from app.api.downloads import build_download_url

    url, exp = build_download_url("contract", "abc")
    assert url.startswith("http"), url
    assert "://" in url, "openLink needs an absolute URL, not a path"
    assert "/api/dl/contract/abc" in url
    assert f"e={exp}" in url and "s=" in url


def test_the_url_carries_a_deadline_in_the_near_future():
    from app.api.downloads import DOWNLOAD_TTL, build_download_url

    _, exp = build_download_url("contract", "abc")
    now = int(datetime.now(UTC).timestamp())
    assert now < exp <= now + int(DOWNLOAD_TTL.total_seconds()) + 5


def test_the_ttl_is_short():
    # A per-click link that lived for hours would be a durable bearer URL over
    # someone's contract. If this ever needs raising, it should be a decision.
    from app.api.downloads import DOWNLOAD_TTL

    assert timedelta(minutes=15) >= DOWNLOAD_TTL


# ── the serve route ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_unsigned_request_is_refused(isolation_ctx):
    client, _ = isolation_ctx
    r = await client.get("/api/dl/contract/00000000-0000-0000-0000-000000000001")
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_a_forged_signature_is_refused(isolation_ctx):
    client, _ = isolation_ctx
    r = await client.get(
        "/api/dl/contract/00000000-0000-0000-0000-000000000001?e=99999999999&s=" + "a" * 32
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_every_failure_looks_the_same_from_outside(isolation_ctx):
    """Forged, tampered and expired must not be distinguishable.

    A different status or detail per failure mode tells a prober which half of
    their guess was right.
    """
    client, _ = isolation_ctx
    ident = "00000000-0000-0000-0000-000000000001"
    expired = int((datetime.now(UTC) - timedelta(hours=1)).timestamp())

    responses = [
        await client.get(f"/api/dl/contract/{ident}?e=99999999999&s={'a' * 32}"),
        await client.get(f"/api/dl/contract/{ident}?e={expired}&s={sign_download('contract', ident, expired)}"),
        await client.get(f"/api/dl/contract/{ident}"),
    ]
    assert {r.status_code for r in responses} == {403}
    assert len({r.json().get("detail") for r in responses}) == 1


@pytest.mark.asyncio
async def test_the_grant_route_requires_a_session(isolation_ctx):
    """The whole scheme rests on the grant being the only authorization point."""
    client, _ = isolation_ctx
    r = await client.post(
        "/api/downloads/grant",
        json={"kind": "contract", "id": "00000000-0000-0000-0000-000000000001"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert r.status_code in (401, 403), r.text


# ── Telegram delivery (#4 of the follow-up brief) ────────────────────────────

@pytest.mark.asyncio
async def test_telegram_delivery_still_requires_a_session(isolation_ctx):
    """`deliver=telegram` must not become an unauthenticated render path."""
    client, _ = isolation_ctx
    r = await client.post(
        "/api/downloads/grant",
        json={"kind": "contract", "id": "00000000-0000-0000-0000-000000000001",
              "deliver": "telegram"},
    )
    assert r.status_code in (401, 403), r.text


@pytest.mark.asyncio
async def test_an_unknown_deliver_mode_is_rejected(isolation_ctx):
    """The mode is a Literal, so anything else is a 422 — not a silent default
    back to handing out a URL."""
    client, ctx = isolation_ctx
    r = await client.post(
        "/api/downloads/grant",
        json={"kind": "contract", "id": "00000000-0000-0000-0000-000000000001",
              "deliver": "carrier-pigeon"},
        headers=ctx["auth_a"],
    )
    assert r.status_code == 422, r.text


def test_both_delivery_paths_render_through_one_function():
    """The signed route and the Telegram hand-off must produce the same bytes.

    Asserted structurally: the per-kind switch lives in `_render` and NOWHERE
    else, so the two delivery paths cannot drift into producing different
    documents for the same object. Checked per-function rather than by counting
    occurrences in the module — each renderer legitimately appears twice inside
    `_render` itself (the deferred import, then the call).
    """
    import inspect

    from app.api import downloads

    renderers = ("render_contract_pdf", "render_receipt_pdf", "render_invoice_pdf")

    render_src = inspect.getsource(downloads._render)
    for r in renderers:
        assert r in render_src, f"{r} is not in _render"

    for fn in (downloads.serve_download, downloads.grant_download):
        src = inspect.getsource(fn)
        for r in renderers:
            assert r not in src, f"{fn.__name__} renders {r} itself instead of calling _render"
        assert "_render(" in src, f"{fn.__name__} does not go through _render"
