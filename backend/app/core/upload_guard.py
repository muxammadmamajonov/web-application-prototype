"""Upload validation for payment proofs: rasters by re-encode, PDFs by parse.

INVARIANT: fail closed. If the application cannot prove a file is a bounded,
passive document, it is rejected.
"""
from __future__ import annotations

from io import BytesIO

#: Raster formats a payment proof may be.
ALLOWED_IMAGE_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})

#: Decoded-pixel ceiling, checked from the image header before any row is
#: expanded — after decoding the memory has already been taken.
MAX_IMAGE_PIXELS = 50_000_000

#: Longest side. Catches aspect ratios that slip under a pixel-count cap.
MAX_IMAGE_DIMENSION = 20_000

MAX_PDF_PAGES = 30

#: Bounds on object-graph traversal. A PDF may contain reference cycles and
#: hostile fan-out, so traversal is capped rather than trusted to terminate.
MAX_PDF_OBJECTS = 20_000
MAX_PDF_DEPTH = 60

#: Dictionary keys that make a PDF active: script execution, external
#: navigation, form submission, or a carried file. A bank slip has none.
#: Compared against DECODED names, so `/Java#53cript` is caught as `/JavaScript`.
_ACTIVE_KEYS = frozenset({
    "/OpenAction", "/AA", "/JavaScript", "/JS", "/Launch", "/SubmitForm",
    "/ImportData", "/GoToR", "/URI", "/RichMedia", "/EmbeddedFile",
    "/EmbeddedFiles", "/Filespec", "/XFA",
})

#: Values that mean the same thing when they appear as an action `/S` subtype
#: or a `/Type`, where the capability is named by the value rather than the key.
_ACTIVE_VALUES = frozenset({
    "/JavaScript", "/Launch", "/SubmitForm", "/ImportData", "/GoToR", "/URI",
    "/RichMedia", "/Filespec", "/EmbeddedFile",
})

#: Leading bytes of formats refused outright, so the error names the real reason.
_HOSTILE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"MZ", "executable"),
    (b"\x7fELF", "executable"),
    (b"#!", "script"),
    (b"PK\x03\x04", "archive"),
    (b"\x1f\x8b", "archive"),
    (b"Rar!", "archive"),
    (b"7z\xbc\xaf", "archive"),
    (b"<?xml", "markup"),
    (b"<svg", "markup"),
    (b"<!DOCTYPE", "markup"),
    (b"<html", "markup"),
    (b"<HTML", "markup"),
)


class UploadRejected(ValueError):
    """``reason`` is a stable machine code describing the caller's own upload."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def looks_hostile(data: bytes) -> str | None:
    """Refusal reason for a format never accepted, or None.

    Leading whitespace is skipped because a sniffing browser skips it too: an
    attacker prefixing a newline must not get a different answer here than the
    browser gives later.
    """
    head = data[:1024].lstrip()[:64]
    for sig, kind in _HOSTILE_SIGNATURES:
        if head.startswith(sig):
            return f"{kind}_not_allowed"
    return None


# ── PDF ─────────────────────────────────────────────────────────────────────

def _reader(data: bytes):
    from pypdf import PdfReader

    return PdfReader(BytesIO(data), strict=False)


def pdf_page_count(data: bytes) -> int:
    """Number of pages, resolved through the real page tree.

    Raises :class:`UploadRejected` rather than returning None: an unknown page
    count is a refusal, not a pass. A regex cannot see a page tree stored in a
    compressed object stream, which is why this goes through a parser.
    """
    try:
        reader = _reader(data)
        if reader.is_encrypted:
            raise UploadRejected("pdf_encrypted")
        return len(reader.pages)
    except UploadRejected:
        raise
    except Exception:
        raise UploadRejected("pdf_unparseable") from None


def _walk_for_active_content(reader) -> None:
    """Traverse reachable objects and refuse any active capability.

    Works on resolved objects, so it sees through compressed object streams and
    through `#`-escaped names — both of which defeat raw-byte matching.
    """
    from pypdf.generic import IndirectObject

    try:
        roots = [reader.trailer]
    except Exception:
        raise UploadRejected("pdf_unparseable") from None

    # An indirect object is identified by (idnum, generation), not idnum alone:
    # a generation bump is a different object at the same number.
    seen: set[tuple[int, int]] = set()
    stack: list[tuple[object, int]] = [(r, 0) for r in roots]
    visited = 0

    while stack:
        node, depth = stack.pop()
        if depth > MAX_PDF_DEPTH:
            raise UploadRejected("pdf_too_deep")
        visited += 1
        if visited > MAX_PDF_OBJECTS:
            raise UploadRejected("pdf_too_many_objects")

        if isinstance(node, IndirectObject):
            key = (node.idnum, node.generation)
            if key in seen:
                continue
            seen.add(key)
            try:
                node = node.get_object()
            except Exception:
                raise UploadRejected("pdf_unparseable") from None

        if isinstance(node, dict):
            for raw_key, value in node.items():
                name = str(raw_key)
                if name in _ACTIVE_KEYS:
                    raise UploadRejected("pdf_active_content")
                if isinstance(value, str) and str(value) in _ACTIVE_VALUES:
                    raise UploadRejected("pdf_active_content")
                stack.append((value, depth + 1))
        elif isinstance(node, list):
            for value in node:
                stack.append((value, depth + 1))


def validate_pdf(data: bytes) -> None:
    """Raise :class:`UploadRejected` unless the bytes are a bounded, passive PDF.

    Deliberately parser-backed. The previous byte-substring approximation could
    not see a page tree or an action inside a compressed object stream, and did
    not decode `#`-escaped names — so it accepted documents it reported as safe.
    """
    if not data.startswith(b"%PDF-"):
        raise UploadRejected("pdf_bad_header")

    try:
        reader = _reader(data)
    except Exception:
        raise UploadRejected("pdf_unparseable") from None

    # Encryption is refused rather than attempted: an encrypted document cannot
    # be inspected, and a document we cannot inspect cannot be proved passive.
    try:
        encrypted = bool(reader.is_encrypted)
    except Exception:
        raise UploadRejected("pdf_unparseable") from None
    if encrypted:
        raise UploadRejected("pdf_encrypted")

    try:
        pages = len(reader.pages)
    except UploadRejected:
        raise
    except Exception:
        raise UploadRejected("pdf_unparseable") from None

    if pages < 1:
        raise UploadRejected("pdf_no_pages")
    if pages > MAX_PDF_PAGES:
        raise UploadRejected("pdf_too_many_pages")

    _walk_for_active_content(reader)


# ── rasters ─────────────────────────────────────────────────────────────────

def check_raster_limits(width: int, height: int) -> None:
    """Refuse a raster whose DECLARED size is a resource attack."""
    if width <= 0 or height <= 0:
        raise UploadRejected("image_bad_dimensions")
    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise UploadRejected("image_too_large_dimensions")
    if width * height > MAX_IMAGE_PIXELS:
        raise UploadRejected("image_too_many_pixels")
