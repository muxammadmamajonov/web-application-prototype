"""SEC-001 — the legacy KYC media fetch must revalidate every redirect hop.

The handler allowlists `archive.org` on the INITIAL url, but the fetch itself used
to follow redirects automatically, so the allowlist was evaluated once and never
re-applied. An open redirect on an allowed host was therefore enough to reach an
internal address and have its body returned to the caller.

These tests mock only the transport and DNS boundary; no real network call is made.
"""
from __future__ import annotations

import httpx
import pytest

from app.core.urlsafety import UnsafeUrlError, fetch_with_safe_redirects

ARCHIVE = "https://archive.org/download/x/y.webp"
ALLOWED = ("archive.org",)


def _dns(mapping: dict[str, str]):
    """Fake resolver: host -> single IP string."""
    def _resolve(host: str) -> list[str]:
        return [mapping[host]] if host in mapping else ["93.184.216.34"]  # public default
    return _resolve


def _transport(routes: dict[str, httpx.Response]):
    """Transport that answers by absolute URL and records what was requested."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        for prefix, resp in routes.items():
            if str(request.url).startswith(prefix):
                return resp
        return httpx.Response(200, content=b"default")

    return httpx.MockTransport(handler), seen


async def test_redirect_to_loopback_is_rejected_and_never_requested() -> None:
    """Slice 1 — an in-family URL that redirects to loopback must not be followed."""
    transport, seen = _transport({
        ARCHIVE: httpx.Response(302, headers={"location": "http://127.0.0.1:8000/admin"}),
    })
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(UnsafeUrlError):
            await fetch_with_safe_redirects(
                client, ARCHIVE, allowed_hosts=ALLOWED, max_redirects=3,
                resolve=_dns({"archive.org": "93.184.216.34"}),
            )
    # The internal target must never have been contacted.
    assert not any("127.0.0.1" in u for u in seen)


@pytest.mark.parametrize("target", [
    "http://10.0.0.5/x",            # private v4
    "http://169.254.169.254/latest",  # link-local / cloud metadata
    "http://[::1]/x",               # loopback v6
    "http://[fd00::1]/x",           # unique-local v6
])
async def test_redirect_into_internal_ranges_is_rejected(target: str) -> None:
    transport, seen = _transport({ARCHIVE: httpx.Response(302, headers={"location": target})})
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(UnsafeUrlError):
            await fetch_with_safe_redirects(
                client, ARCHIVE, allowed_hosts=ALLOWED, max_redirects=3,
                resolve=_dns({"archive.org": "93.184.216.34"}),
            )
    assert len(seen) == 1  # only the initial, allowed request happened


async def test_redirect_to_public_but_non_allowlisted_host_is_rejected() -> None:
    """Slice 2 — leaving the archive.org family is rejected even if the host is public."""
    transport, seen = _transport({
        ARCHIVE: httpx.Response(302, headers={"location": "https://evil.example/y.webp"}),
    })
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(UnsafeUrlError):
            await fetch_with_safe_redirects(
                client, ARCHIVE, allowed_hosts=ALLOWED, max_redirects=3,
                resolve=_dns({}),
            )
    assert not any("evil.example" in u for u in seen)


async def test_in_family_redirect_still_succeeds() -> None:
    """Slice 3 — legitimate archive.org → *.archive.org CDN redirects keep working."""
    cdn = "https://ia801504.us.archive.org/download/x/y.webp"
    transport, seen = _transport({
        ARCHIVE: httpx.Response(302, headers={"location": cdn}),
        cdn: httpx.Response(200, content=b"IMAGEBYTES", headers={"content-type": "image/webp"}),
    })
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await fetch_with_safe_redirects(
            client, ARCHIVE, allowed_hosts=ALLOWED, max_redirects=3, resolve=_dns({}),
        )
    assert resp.status_code == 200
    assert resp.content == b"IMAGEBYTES"
    assert resp.headers["content-type"] == "image/webp"
    assert len(seen) == 2


async def test_redirect_loop_is_bounded() -> None:
    """Slice 4 — a redirect loop terminates at the existing hop bound, not by hanging."""
    loop_a = "https://archive.org/a"
    loop_b = "https://archive.org/b"
    transport, seen = _transport({
        loop_a: httpx.Response(302, headers={"location": loop_b}),
        loop_b: httpx.Response(302, headers={"location": loop_a}),
    })
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(UnsafeUrlError):
            await fetch_with_safe_redirects(
                client, loop_a, allowed_hosts=ALLOWED, max_redirects=3, resolve=_dns({}),
            )
    assert len(seen) <= 4  # initial + at most max_redirects hops


async def test_credentials_in_url_are_rejected() -> None:
    """A userinfo-bearing URL is refused outright rather than silently sent."""
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
        with pytest.raises(UnsafeUrlError):
            await fetch_with_safe_redirects(
                client, "https://user:pw@archive.org/x", allowed_hosts=ALLOWED,
                max_redirects=3, resolve=_dns({}),
            )


async def test_non_http_scheme_is_rejected() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
        with pytest.raises(UnsafeUrlError):
            await fetch_with_safe_redirects(
                client, "file:///etc/passwd", allowed_hosts=ALLOWED,
                max_redirects=3, resolve=_dns({}),
            )
