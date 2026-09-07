"""
Phase 1: Auth Error Paths — OIDC Callback Integration Tests

Tests ensure the `/auth/telegram/callback` endpoint correctly handles all error paths
during token exchange, JWT verification, and session establishment.

Target: 6 tests covering OAuth error responses, network failures, JWT validation,
and replay/nonce protection.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.config import settings


@pytest.fixture(autouse=True)
def setup_oidc_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure OIDC credentials are set for all tests in this module."""
    monkeypatch.setattr(settings, "telegram_login_client_id", "test-client-id", raising=False)
    monkeypatch.setattr(settings, "telegram_login_client_secret", "test-client-secret", raising=False)


@pytest.fixture(autouse=True)
def _allow_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The OIDC start/callback routes now carry ``login_limit()`` (audit). Give the
    rate limiter a working Redis stub so it passes (count=1, under the limit) and
    the handler error paths below are exercised — otherwise, with no Redis in the
    unit tier, the fail-closed limiter would short-circuit every request to 503."""
    class _LimiterRedis:
        async def eval(self, *_a: object, **_k: object) -> int:
            return 1
    monkeypatch.setattr("app.core.ratelimit.get_redis", lambda: _LimiterRedis())


@pytest.mark.asyncio
async def test_callback_state_unknown_400(async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Unknown or expired state → 400 Bad Request.
    Prevents replay attacks and CSRF by rejecting reused/forged state.
    """
    # Mock Redis.get to return None (state not found)
    async def mock_redis_get(key: str) -> None:
        return None

    def mock_get_redis():
        """get_redis is NOT async, returns a Redis-like object."""
        mock_redis = AsyncMock()
        mock_redis.get = mock_redis_get
        return mock_redis

    monkeypatch.setattr("app.api.auth_oidc.get_redis", mock_get_redis)

    # MUDD-25 H3: the callback now requires the state to match an HttpOnly
    # CSRF cookie before it ever looks at Redis — set it so this test still
    # exercises the "unknown state" branch instead of the cookie-mismatch one.
    async_client.cookies.set("muddato.oidc_state", "unknown_state")
    response = await async_client.post(
        "/api/auth/telegram/callback",
        json={"code": "test_code", "state": "unknown_state"}
    )

    assert response.status_code == 400
    data = response.json()
    assert "state_expired_or_unknown" in data.get("detail", "")


@pytest.mark.asyncio
async def test_callback_token_exchange_network_error_502(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Network failure reaching OAuth token endpoint → 502 Bad Gateway.
    Differentiates network failures from authentication failures.
    """
    stashed = "test_verifier|https://muddato.uz/auth/callback|test_nonce"

    async def mock_redis_get(key: str) -> str | None:
        if "oidc:state:" in key:
            return stashed
        return None

    async def mock_redis_delete(key: str) -> None:
        pass

    def mock_get_redis():
        """get_redis is NOT async, returns a Redis-like object."""
        mock_redis = AsyncMock()
        mock_redis.get = mock_redis_get
        mock_redis.delete = mock_redis_delete
        return mock_redis

    monkeypatch.setattr("app.api.auth_oidc.get_redis", mock_get_redis)

    # Mock httpx to raise a connection error
    async def mock_post(*args, **kwargs):
        import httpx
        raise httpx.ConnectError("Connection refused")

    with patch("app.api.auth_oidc.httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = mock_post
        mock_client_class.return_value = mock_client

        async_client.cookies.set("muddato.oidc_state", "test_state")
        response = await async_client.post(
            "/api/auth/telegram/callback",
            json={"code": "test_code", "state": "test_state"}
        )

    assert response.status_code == 502
    data = response.json()
    assert "token_exchange_unreachable" in data.get("detail", "")


@pytest.mark.asyncio
async def test_callback_oauth_error_response_401(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    OAuth error response ({"error": "invalid_grant", ...}) → 401 with detailed message.
    Exposes standard OAuth error codes so issues are actionable (not opaque).
    """
    stashed = "test_verifier|https://muddato.uz/auth/callback|test_nonce"

    async def mock_redis_get(key: str) -> str | None:
        if "oidc:state:" in key:
            return stashed
        return None

    async def mock_redis_delete(key: str) -> None:
        pass

    def mock_get_redis():
        """get_redis is NOT async, returns a Redis-like object."""
        mock_redis = AsyncMock()
        mock_redis.get = mock_redis_get
        mock_redis.delete = mock_redis_delete
        return mock_redis

    monkeypatch.setattr("app.api.auth_oidc.get_redis", mock_get_redis)

    # Mock httpx to return an OAuth error
    async def mock_post(*args, **kwargs):
        # Create a real mock object, not AsyncMock
        response = MagicMock()
        response.status_code = 200
        response.json = MagicMock(return_value={
            "error": "invalid_grant",
            "error_description": "Authorization code expired"
        })
        response.text = '{"error": "invalid_grant"}'
        return response

    with patch("app.api.auth_oidc.httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = mock_post
        mock_client_class.return_value = mock_client

        async_client.cookies.set("muddato.oidc_state", "test_state")
        response = await async_client.post(
            "/api/auth/telegram/callback",
            json={"code": "expired_code", "state": "test_state"}
        )

    assert response.status_code == 401
    data = response.json()
    detail = data.get("detail", "")
    assert "telegram_oauth_error" in detail and "invalid_grant" in detail


@pytest.mark.asyncio
async def test_callback_no_id_token_401(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    OAuth response missing id_token and no valid JWT access_token → 401 Unauthorized.
    Prevents identity forge by rejecting unsigned/unverifiable responses.
    """
    stashed = "test_verifier|https://muddato.uz/auth/callback|test_nonce"

    async def mock_redis_get(key: str) -> str | None:
        if "oidc:state:" in key:
            return stashed
        return None

    async def mock_redis_delete(key: str) -> None:
        pass

    def mock_get_redis():
        """get_redis is NOT async, returns a Redis-like object."""
        mock_redis = AsyncMock()
        mock_redis.get = mock_redis_get
        mock_redis.delete = mock_redis_delete
        return mock_redis

    monkeypatch.setattr("app.api.auth_oidc.get_redis", mock_get_redis)

    # Mock httpx to return a response without id_token
    async def mock_post(*args, **kwargs):
        response = MagicMock()
        response.status_code = 200
        response.json = MagicMock(return_value={
            "access_token": "opaque_string_not_jwt",
            "token_type": "Bearer"
        })
        response.text = '{"access_token": "..."}'
        return response

    with patch("app.api.auth_oidc.httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = mock_post
        mock_client_class.return_value = mock_client

        async_client.cookies.set("muddato.oidc_state", "test_state")
        response = await async_client.post(
            "/api/auth/telegram/callback",
            json={"code": "test_code", "state": "test_state"}
        )

    assert response.status_code == 401
    data = response.json()
    assert "id_token_missing" in data.get("detail", "")


@pytest.mark.asyncio
async def test_callback_jwt_verification_failed_401(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    JWT verification fails (invalid signature, wrong issuer/audience, expired) → 401 Unauthorized.
    Fail-closed: never accept unverified claims (prevents auth bypass if token-endpoint is compromised).
    """
    stashed = "test_verifier|https://muddato.uz/auth/callback|test_nonce"

    async def mock_redis_get(key: str) -> str | None:
        if "oidc:state:" in key:
            return stashed
        return None

    async def mock_redis_delete(key: str) -> None:
        pass

    def mock_get_redis():
        """get_redis is NOT async, returns a Redis-like object."""
        mock_redis = AsyncMock()
        mock_redis.get = mock_redis_get
        mock_redis.delete = mock_redis_delete
        return mock_redis

    monkeypatch.setattr("app.api.auth_oidc.get_redis", mock_get_redis)

    # Mock httpx to return a JWT that will fail verification
    bad_jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    )

    async def mock_post(*args, **kwargs):
        response = MagicMock()
        response.status_code = 200
        response.json = MagicMock(return_value={"id_token": bad_jwt})
        response.text = f'{{"id_token": "{bad_jwt}"}}'
        return response

    async def mock_empty_jwks(*, force: bool = False) -> dict:
        return {"keys": []}

    monkeypatch.setattr("app.api.auth_oidc._get_jwks", mock_empty_jwks)

    with patch("app.api.auth_oidc.httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = mock_post
        mock_client_class.return_value = mock_client

        async_client.cookies.set("muddato.oidc_state", "test_state")
        response = await async_client.post(
            "/api/auth/telegram/callback",
            json={"code": "test_code", "state": "test_state"}
        )

    assert response.status_code == 401
    data = response.json()
    assert "id_token_kid_unknown" in data.get("detail", "")


@pytest.mark.asyncio
async def test_callback_redis_unavailable_503(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Redis unavailable during state retrieval → 503 Service Unavailable.
    Gracefully handles transient infrastructure failures.
    """
    async def mock_redis_get(key: str) -> None:
        raise Exception("Redis connection timeout")

    def mock_get_redis():
        """get_redis is NOT async, returns a Redis-like object."""
        mock_redis = AsyncMock()
        mock_redis.get = mock_redis_get
        return mock_redis

    monkeypatch.setattr("app.api.auth_oidc.get_redis", mock_get_redis)

    async_client.cookies.set("muddato.oidc_state", "test_state")
    response = await async_client.post(
        "/api/auth/telegram/callback",
        json={"code": "test_code", "state": "test_state"}
    )

    assert response.status_code == 503
    data = response.json()
    assert "auth_store_unavailable" in data.get("detail", "")


# ─────────────────────────────────────────────────────────────────────────────
# MUDD-25 H3: OIDC CSRF State Binding — real regression tests, not placeholders.
# The two below are the actual CSRF vector H3 closes: a forged/absent state
# cookie must be rejected BEFORE Redis is ever consulted.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_callback_missing_state_cookie_400(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No CSRF cookie at all (e.g. attacker drives the callback cross-site with a
    stolen/guessed `state` but never held the `/telegram/start` cookie) → 400,
    and Redis must never be consulted."""
    stashed = "test_verifier|https://muddato.uz/auth/callback|test_nonce"
    redis_get_called = False

    async def mock_redis_get(key: str) -> str | None:
        nonlocal redis_get_called
        redis_get_called = True
        return stashed

    def mock_get_redis():
        mock_redis = AsyncMock()
        mock_redis.get = mock_redis_get
        return mock_redis

    monkeypatch.setattr("app.api.auth_oidc.get_redis", mock_get_redis)

    async_client.cookies.delete("muddato.oidc_state")
    response = await async_client.post(
        "/api/auth/telegram/callback",
        json={"code": "test_code", "state": "test_state"}
    )

    assert response.status_code == 400
    data = response.json()
    assert "state_mismatch_or_missing" in data.get("detail", "")
    assert redis_get_called is False


@pytest.mark.asyncio
async def test_callback_state_cookie_mismatch_400(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cookie present but doesn't match the posted `state` (classic CSRF: victim's
    browser carries their own cookie, attacker supplies their own state/code
    pair) → 400, and Redis must never be consulted."""
    stashed = "test_verifier|https://muddato.uz/auth/callback|test_nonce"
    redis_get_called = False

    async def mock_redis_get(key: str) -> str | None:
        nonlocal redis_get_called
        redis_get_called = True
        return stashed

    def mock_get_redis():
        mock_redis = AsyncMock()
        mock_redis.get = mock_redis_get
        return mock_redis

    monkeypatch.setattr("app.api.auth_oidc.get_redis", mock_get_redis)

    async_client.cookies.set("muddato.oidc_state", "victims_own_state")
    response = await async_client.post(
        "/api/auth/telegram/callback",
        json={"code": "attacker_code", "state": "attackers_state"}
    )

    assert response.status_code == 400
    data = response.json()
    assert "state_mismatch_or_missing" in data.get("detail", "")
    assert redis_get_called is False
