"""Request-size guards (#88).

A 512MB worker must never read a multi-GB body into memory before returning a
413. Two layers:

* ``MaxBodySizeMiddleware`` rejects an oversized request on its Content-Length
  header *before* the body is read (the cheap, global guard).
* ``read_capped`` streams an UploadFile in chunks and aborts the moment it
  exceeds the per-endpoint cap (defense for chunked / Content-Length-less
  uploads that the middleware can't size upfront).
"""
from __future__ import annotations

from fastapi import HTTPException, UploadFile, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# Global hard cap on any request body — comfortably above the per-file caps so a
# legit multipart upload fits, small enough that one request can't OOM the worker.
MAX_BODY_BYTES = 16 * 1024 * 1024
MAX_UPLOAD_BYTES = 8 * 1024 * 1024      # a single image / receipt photo
# Must equal IMPORT_FILE.maxBytes in frontend/src/lib/fileValidation.ts. The
# import page has always TOLD the user "max 5MB" while the server accepted 8 —
# so a 6 MB workbook was uploaded, parsed and only then judged by a rule the UI
# had already contradicted. Pinned across the two languages by
# frontend/src/lib/importLimit.contract.test.ts.
MAX_IMPORT_BYTES = 5 * 1024 * 1024      # an xlsx / csv import file
MAX_IMPORT_ROWS = 10_000                # rows per import (post-parse cap)


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                size = int(cl)
            except ValueError:
                return JSONResponse({"detail": "bad_content_length"}, status_code=400)
            if size > MAX_BODY_BYTES:
                return JSONResponse({"detail": "request_too_large"}, status_code=413)
        return await call_next(request)


async def read_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Read ``file`` in 64KB chunks, raising 413 as soon as it exceeds
    ``max_bytes`` — so a huge upload never fully lands in memory even if it lied
    about (or omitted) its Content-Length."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1 << 16):
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "file_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


# ── top-up receipt file types (#9) ───────────────────────────────────────────

# Must equal RECEIPT_FILE in frontend/src/lib/fileValidation.ts, which is what
# populates the picker's `accept` and rejects the file before it is ever sent.
# This is the second line: `accept` is a hint a determined caller can ignore, and
# the file lands in an admin's review queue where it is opened by hand.
RECEIPT_CONTENT_TYPES = frozenset({
    "application/pdf", "image/png", "image/jpeg",
})
RECEIPT_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg")


def receipt_type_ok(filename: str | None, content_type: str | None) -> bool:
    """Accept only a PDF or a PNG/JPEG image.

    Both signals are checked, and either one being right is enough: browsers
    disagree about the content type of a `.jpg` (``image/jpeg`` vs the
    occasional ``application/octet-stream`` from a Telegram WebView), so
    demanding both would reject genuine uploads. Requiring at least one keeps
    an executable or archive out while staying usable on a phone.
    """
    name = (filename or "").lower()
    if name.endswith(RECEIPT_EXTENSIONS):
        return True
    return (content_type or "").split(";")[0].strip().lower() in RECEIPT_CONTENT_TYPES
