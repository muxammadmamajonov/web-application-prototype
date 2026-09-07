"""#78 — every genuinely-unbounded list endpoint caps its page size.

A regression guard via the OpenAPI schema: each of these list routes must expose
a `limit` query param with a `maximum`, so a caller can't request an unbounded
page (the OOM/DoS the issue is about). Inherently-bounded lists (5-plan cap,
singleton settings, per-customer detail) are intentionally not listed.
"""
from __future__ import annotations

CAPPED_LIST_PATHS = [
    "/api/me/installments",
    "/api/catalog/models",
    "/api/platform/promos",
    "/api/platform/ads",
    "/api/platform/referrals",
    "/api/tenant/invoices",
]


def test_list_endpoints_cap_their_limit() -> None:
    from app.main import app

    paths = app.openapi()["paths"]
    for path in CAPPED_LIST_PATHS:
        assert path in paths, f"{path} missing from OpenAPI"
        params = paths[path]["get"].get("parameters", [])
        limit = next((p for p in params if p.get("name") == "limit"), None)
        assert limit is not None, f"{path}: no `limit` query param"
        maximum = limit["schema"].get("maximum")
        assert maximum is not None and maximum <= 200, f"{path}: `limit` unbounded / cap too high"
