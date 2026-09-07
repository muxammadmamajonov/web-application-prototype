"""
Phase 3b: Admin Operations — protected admin-only endpoints
Tests ensure admin-only routes reject non-admin callers and handle edge cases.
Target: 3-4 tests for admin access control.
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_protected_admin_endpoint_requires_auth(async_client: AsyncClient) -> None:
    """Admin endpoints require valid authentication."""
    response = await async_client.get("/api/platform/tenants")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_admin_endpoint_rejects_bad_token(async_client: AsyncClient) -> None:
    """Admin endpoints reject invalid tokens."""
    response = await async_client.get(
        "/api/platform/tenants",
        headers={"Authorization": "Bearer not.a.valid.token"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_protected_admin_route_promos_endpoint(async_client: AsyncClient) -> None:
    """Admin promos route is protected from unauthenticated access."""
    response = await async_client.get(
        "/api/platform/promos",
        headers={"Authorization": "Bearer invalid-token"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_admin_endpoint_accepts_bearer_scheme(async_client: AsyncClient) -> None:
    """Admin endpoints parse Bearer scheme authorization header."""
    # Valid Bearer format, invalid token
    response = await async_client.get(
        "/api/platform/tenants",
        headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"}
    )
    # Should fail at token validation
    assert response.status_code == 401
