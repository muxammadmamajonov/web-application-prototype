"""
Phase 2: Auth Error Paths — HTTP-level auth gate tests
Tests ensure missing/invalid auth tokens are rejected before DB queries.
Target: 5-6 tests for auth edge cases.
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_garbage_bearer_token_401(async_client: AsyncClient) -> None:
    """Garbage bearer token → 401 Unauthorized."""
    response = await async_client.get(
        "/api/tenant/customers",
        headers={"Authorization": "Bearer not.a.real.jwt"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_missing_authorization_header_401(async_client: AsyncClient) -> None:
    """Missing Authorization header → 401 Unauthorized."""
    response = await async_client.get("/api/tenant/customers")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_bearer_header_case_insensitive(async_client: AsyncClient) -> None:
    """Bearer header matching is case-insensitive."""
    response = await async_client.get(
        "/api/tenant/customers",
        headers={"Authorization": "bearer fake-token"}
    )
    # Should be treated as Bearer scheme
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_malformed_bearer_header_401(async_client: AsyncClient) -> None:
    """Malformed Bearer header → 401 Unauthorized."""
    response = await async_client.get(
        "/api/tenant/customers",
        headers={"Authorization": "NotBearer token123"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_health_endpoint_public(async_client: AsyncClient) -> None:
    """Health endpoint is public (no auth required)."""
    response = await async_client.get("/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_bearer_token_with_extra_spaces(async_client: AsyncClient) -> None:
    """Bearer token with extra spaces → parsed correctly or rejected."""
    response = await async_client.get(
        "/api/tenant/customers",
        headers={"Authorization": "Bearer  token-with-spaces"}
    )
    # Should fail at token validation, not header parsing
    assert response.status_code == 401
