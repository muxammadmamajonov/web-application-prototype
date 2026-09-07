"""#741 — runtime CORS behaviour: never `Access-Control-Allow-Origin: *` with credentials.

The config side (explicit `allowed_origins`, production boot-guard refusing
wildcard) is covered by test_p2_api_hardening. But the #741 *runtime* tests live
in a skip-marked file (test_p1_wave1_hardening), so the actual response header —
the literal subject of #741 ("sends ACAO: * with credentials=true") — had no live
coverage. These hit the real ASGI app and assert the CORS middleware:

  * echoes a whitelisted Origin back specifically (never "*"), with credentials,
  * does NOT reflect an untrusted Origin (no ACAO header at all → browser blocks).

DB-free: rides the ASGITransport `async_client` fixture; `/health` needs no DB.
The test-env allowlist is the config default, `http://localhost:5173`.
"""
from __future__ import annotations

import httpx

ALLOWED_ORIGIN = "http://localhost:5173"
EVIL_ORIGIN = "https://evil.example.com"


async def test_cors_echoes_allowed_origin_never_wildcard(async_client: httpx.AsyncClient) -> None:
    resp = await async_client.get("/health", headers={"Origin": ALLOWED_ORIGIN})
    acao = resp.headers.get("access-control-allow-origin")
    # The specific whitelisted origin is echoed — NOT "*", which the #741 spec
    # violation (wildcard + credentials) would have produced.
    assert acao == ALLOWED_ORIGIN
    assert acao != "*"
    # Credentials are only ever advertised alongside a specific origin.
    assert resp.headers.get("access-control-allow-credentials") == "true"


async def test_cors_does_not_reflect_untrusted_origin(async_client: httpx.AsyncClient) -> None:
    resp = await async_client.get("/health", headers={"Origin": EVIL_ORIGIN})
    acao = resp.headers.get("access-control-allow-origin")
    # An origin not on the allowlist must NOT be reflected, and never "*".
    assert acao != "*"
    assert acao != EVIL_ORIGIN
    assert acao is None  # Starlette omits ACAO entirely for a disallowed origin


async def test_cors_preflight_allows_specific_origin_with_credentials(
    async_client: httpx.AsyncClient,
) -> None:
    resp = await async_client.options(
        "/api/auth/telegram",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
        },
    )
    acao = resp.headers.get("access-control-allow-origin")
    assert acao == ALLOWED_ORIGIN
    assert acao != "*"
    assert resp.headers.get("access-control-allow-credentials") == "true"
