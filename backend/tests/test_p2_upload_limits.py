"""#88 — upload size enforced before the body is read + a global max-body limit.

DB-free: read_capped aborts at the cap; the middleware rejects on Content-Length
before passing through; and the middleware is wired into the app.
"""
from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest


class _FakeUpload:
    """Minimal UploadFile stand-in — only .read(n) is exercised by read_capped."""

    def __init__(self, data: bytes) -> None:
        self._buf = BytesIO(data)

    async def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)


async def test_read_capped_aborts_over_limit() -> None:
    from fastapi import HTTPException

    from app.core.limits import read_capped

    with pytest.raises(HTTPException) as ei:
        await read_capped(_FakeUpload(b"x" * 1000), max_bytes=500)  # type: ignore[arg-type]
    assert ei.value.status_code == 413


async def test_read_capped_returns_under_limit() -> None:
    from app.core.limits import read_capped

    data = b"hello world"
    assert await read_capped(_FakeUpload(data), max_bytes=1000) == data  # type: ignore[arg-type]


async def test_max_body_middleware_rejects_on_content_length() -> None:
    from app.core.limits import MAX_BODY_BYTES, MaxBodySizeMiddleware

    mw = MaxBodySizeMiddleware(app=lambda *a: None)
    req = SimpleNamespace(headers={"content-length": str(MAX_BODY_BYTES + 1)})
    called = False

    async def _call_next(_req):
        nonlocal called
        called = True
        return None

    resp = await mw.dispatch(req, _call_next)  # type: ignore[arg-type]
    assert resp.status_code == 413
    assert not called  # rejected before the body was read


async def test_max_body_middleware_passes_small_request() -> None:
    from app.core.limits import MaxBodySizeMiddleware

    mw = MaxBodySizeMiddleware(app=lambda *a: None)
    req = SimpleNamespace(headers={"content-length": "100"})
    sentinel = object()

    async def _call_next(_req):
        return sentinel

    assert await mw.dispatch(req, _call_next) is sentinel  # type: ignore[arg-type]


def test_middleware_wired_into_app() -> None:
    from app.core.limits import MaxBodySizeMiddleware
    from app.main import app

    assert any(m.cls is MaxBodySizeMiddleware for m in app.user_middleware)
