"""Shared SSRF guard: one predicate for every server-side outbound fetch.

Two callers rely on this — the PDF renderer's WeasyPrint url_fetcher and the legacy
KYC media proxy. They previously carried separate checks, which is how the KYC path
ended up validating its host allowlist once and then following redirects blindly.
Keeping the address predicate in one place is the point of this module: a fix here
applies to every consumer.

The address rules are deliberately strict — a resolved address that is private,
loopback, link-local (which includes the cloud metadata endpoint), reserved,
multicast or unspecified is refused, for IPv4 and IPv6 alike. Hostnames are
resolved and EVERY returned address is checked, so a DNS answer mixing a public
and an internal record cannot slip through.

LIMITATION — this is NOT DNS-rebinding protection, and must not be described as
such. We validate one resolution; httpcore then calls ``connect_tcp(host=…)``
with the HOSTNAME, so the socket layer resolves again independently. A DNS answer
that changes between those two lookups reaches an address we never checked.
Binding the two would require rewriting the URL to a literal IP while carrying the
original ``Host`` and TLS SNI through a custom transport — fragile, and a
certificate-verification risk out of proportion to the exposure here. The residual
is confined to the ONE caller that fetches an externally-hosted URL: the legacy
KYC archive.org branch. New KYC media is a private ``KycBlob`` id, not a URL, so
the correct closure is to backfill/remove the remaining legacy rows — after which
this branch, and the residual with it, disappears.
"""
from __future__ import annotations

import ipaddress
import socket
import urllib.parse
from collections.abc import Callable, Sequence

import httpx


class UnsafeUrlError(ValueError):
    """A URL was refused before any request left the process."""


def _default_resolve(host: str) -> list[str]:
    """Every address the host resolves to, v4 and v6."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise UnsafeUrlError(f"unresolvable host: {host}") from exc
    return [str(info[4][0]) for info in infos]


def is_public_address(addr: str) -> bool:
    """False for any address a server-side fetch must never reach."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        return False
    if ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return False
    # An IPv4-mapped/compatible v6 address must be judged on the embedded v4.
    mapped = getattr(ip, "ipv4_mapped", None) or getattr(ip, "sixtofour", None)
    return mapped is None or is_public_address(str(mapped))


def assert_public_host(host: str, *, resolve: Callable[[str], list[str]] | None = None) -> None:
    """Raise unless every address ``host`` resolves to is publicly routable."""
    resolver = resolve or _default_resolve
    addrs = resolver(host)
    if not addrs:
        raise UnsafeUrlError(f"unresolvable host: {host}")
    for addr in addrs:
        if not is_public_address(addr):
            raise UnsafeUrlError(f"blocked private/internal address for host {host}")


def _host_allowed(host: str, allowed_hosts: Sequence[str]) -> bool:
    host = host.lower()
    return any(host == a or host.endswith("." + a) for a in (h.lower() for h in allowed_hosts))


def assert_safe_http_url(
    url: str,
    *,
    allowed_hosts: Sequence[str],
    resolve: Callable[[str], list[str]] | None = None,
) -> None:
    """Validate scheme, credentials, host allowlist and resolved addresses."""
    parsed = urllib.parse.urlsplit(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise UnsafeUrlError(f"blocked url scheme {scheme!r}")
    # Credentials in a URL are never legitimate here and would be sent upstream.
    if parsed.username or parsed.password:
        raise UnsafeUrlError("blocked url with credentials")
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("blocked url with no host")
    if not _host_allowed(host, allowed_hosts):
        raise UnsafeUrlError(f"host not allowed: {host}")
    assert_public_host(host, resolve=resolve)


async def fetch_with_safe_redirects(
    client: httpx.AsyncClient,
    url: str,
    *,
    allowed_hosts: Sequence[str],
    max_redirects: int,
    resolve: Callable[[str], list[str]] | None = None,
) -> httpx.Response:
    """GET ``url``, resolving redirects manually so each hop is revalidated.

    ``follow_redirects`` is deliberately off: httpx would apply the allowlist only
    to the URL we hand it, and every subsequent Location would be fetched
    unchecked. Each hop is re-run through the full predicate before it is
    requested, and the hop budget is bounded so a redirect loop terminates.
    """
    current = url
    for _ in range(max_redirects + 1):
        assert_safe_http_url(current, allowed_hosts=allowed_hosts, resolve=resolve)
        resp = await client.get(current, follow_redirects=False)
        if resp.status_code not in (301, 302, 303, 307, 308):
            return resp
        location = resp.headers.get("location")
        if not location:
            raise UnsafeUrlError("redirect without a location header")
        # Relative Locations are resolved against the hop we just made.
        current = str(httpx.URL(current).join(location))
    raise UnsafeUrlError("too many redirects")
