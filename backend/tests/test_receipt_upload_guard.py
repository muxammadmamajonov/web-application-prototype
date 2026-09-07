"""What a receipt upload is allowed to be.

REPRODUCED ON DESKTOP (founder beta): the receipt picker accepted arbitrary
files, HTML among them. The picker is cosmetic — a determined caller posts to
the endpoint directly — so everything that matters is here, on the bytes.

The threat is not "a shop uploads a weird file". It is that a payment proof is
stored, then served back to a reviewer who is signed in. Anything that can
execute in that reviewer's context — HTML, SVG with a script, a polyglot that a
sniffing browser decides is HTML — is a session-stealing vector aimed at the
one account that can approve money.

So the rule is allowlist-by-decode: we do not ask what the file claims to be,
we decode it and re-encode it. What survives that round trip is, by
construction, an image. What cannot is refused.
"""
from __future__ import annotations

import io
import zlib

import pytest
from fastapi import HTTPException

from app.services.media import sanitize_receipt


def _png(w: int = 8, h: int = 8) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 40, 40)).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg(w: int = 8, h: int = 8) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 90, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def _webp(w: int = 8, h: int = 8) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 160, 90)).save(buf, format="WEBP")
    return buf.getvalue()


def _pdf(pages: int = 1) -> bytes:
    """A minimal but STRUCTURALLY REAL PDF — not just a header.

    Built by hand rather than with a library so the test owns exactly what is
    and is not in the file: the point of several cases below is that a header
    alone must not be enough to get in.
    """
    objs: list[bytes] = []
    kids = " ".join(f"{3 + i} 0 R" for i in range(pages))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    for _ in range(pages):
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _png_header(w: int, h: int) -> bytes:
    """A PNG whose IHDR declares `w`×`h` over a single compressible row.

    The declared size is what a decoder reads first, and refusing there is the
    only safe moment — after the rows are expanded the memory has been taken.
    """
    def chunk(tag: bytes, body: bytes) -> bytes:
        return (len(body).to_bytes(4, "big") + tag + body
                + zlib.crc32(tag + body).to_bytes(4, "big"))

    ihdr = w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes([8, 0, 0, 0, 0])
    raw = b"\x00" + b"\x00" * w
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _decompression_bomb() -> bytes:
    """A tiny PNG that decodes to an enormous raster.

    Hand-built: a real 25000×25000 image would be too big to keep in a repo,
    but the IHDR is what a decoder reads first, so a truthful header over a
    single highly-compressible IDAT is the whole attack.
    """
    def chunk(tag: bytes, body: bytes) -> bytes:
        return (len(body).to_bytes(4, "big") + tag + body
                + zlib.crc32(tag + body).to_bytes(4, "big"))

    w = h = 25_000
    ihdr = w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes([8, 0, 0, 0, 0])
    raw = b"".join(b"\x00" + b"\x00" * w for _ in range(4))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _rejected(data: bytes, content_type: str | None = None) -> HTTPException:
    with pytest.raises(HTTPException) as ei:
        sanitize_receipt(data, content_type)
    assert ei.value.status_code in (400, 413), ei.value.detail
    return ei.value


# ── what must be accepted ───────────────────────────────────────────────────

class TestAcceptedFormats:
    @pytest.mark.parametrize("factory", [_png, _jpeg, _webp])
    def test_the_three_raster_formats_are_accepted_and_normalised(self, factory):
        clean, ct = sanitize_receipt(factory(), "image/png")
        # Re-encoded, not passed through: that round trip IS the sanitisation.
        assert ct == "image/webp"
        assert clean[:4] == b"RIFF"

    @pytest.mark.parametrize("fmt,ext", [("GIF", "gif"), ("BMP", "bmp"), ("TIFF", "tiff")])
    def test_a_format_we_do_not_accept_is_refused_even_though_pillow_reads_it(
        self, fmt, ext,
    ):
        """The allowlist is doing work of its own.

        Pillow decodes far more than four formats, and re-encoding makes any of
        them harmless — so this is not about safety, it is about surface. Every
        format we accept is a decoder we are exposed to, and nobody photographs
        a bank slip into a TIFF.
        """
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (5, 5, 5)).save(buf, format=fmt)
        exc = _rejected(buf.getvalue(), f"image/{ext}")
        assert exc.detail == "unsupported_image_type"

    def test_a_bank_slip_pdf_is_accepted(self):
        clean, ct = sanitize_receipt(_pdf(), "application/pdf")
        assert ct == "application/pdf"
        assert clean[:5] == b"%PDF-"

    def test_a_multi_page_bank_statement_is_accepted(self):
        _clean, ct = sanitize_receipt(_pdf(pages=6), "application/pdf")
        assert ct == "application/pdf"


# ── active content ──────────────────────────────────────────────────────────

class TestActiveContentIsRefused:
    def test_html_is_refused(self):
        # The exact file the founder was able to pick on desktop.
        _rejected(b"<!doctype html><html><body><h1>chek</h1></body></html>", "text/html")

    def test_html_claiming_to_be_a_jpeg_is_still_refused(self):
        # A declared content-type is a claim by the uploader, nothing more.
        _rejected(b"<html><script>fetch('/api/me')</script></html>", "image/jpeg")

    def test_svg_is_refused_even_though_it_is_an_image(self):
        # SVG is a document format with script execution. "It renders in an
        # <img>" is exactly what makes it dangerous, not what makes it safe.
        svg = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        _rejected(svg, "image/svg+xml")

    def test_bare_javascript_is_refused(self):
        _rejected(b"alert(document.cookie)", "application/javascript")

    @pytest.mark.parametrize("payload,label", [
        (b"MZ\x90\x00\x03" + b"\x00" * 64, "windows executable"),
        (b"\x7fELF\x02\x01\x01" + b"\x00" * 64, "linux executable"),
        (b"#!/bin/sh\nrm -rf /\n", "shell script"),
    ])
    def test_executables_are_refused(self, payload, label):
        _rejected(payload, "application/octet-stream")

    @pytest.mark.parametrize("payload,label", [
        (b"PK\x03\x04" + b"\x00" * 32, "zip"),
        (b"\x1f\x8b\x08" + b"\x00" * 32, "gzip"),
        (b"Rar!\x1a\x07\x00" + b"\x00" * 32, "rar"),
    ])
    def test_archives_are_refused(self, payload, label):
        _rejected(payload, "application/zip")

    def test_an_empty_file_is_refused(self):
        _rejected(b"", "image/png")


# ── polyglots and spoofing ──────────────────────────────────────────────────

class TestPolyglotsAndSpoofing:
    def test_a_pdf_header_over_html_is_not_a_pdf(self):
        # THE defect in the old guard: it checked five bytes and passed the
        # rest through untouched. Everything after `%PDF-` was arbitrary.
        _rejected(b"%PDF-1.4\n<html><script>alert(1)</script></html>", "application/pdf")

    def test_a_pdf_header_with_no_structure_is_refused(self):
        _rejected(b"%PDF-1.4\nnot really a pdf at all", "application/pdf")

    def test_html_hidden_after_a_valid_png_is_not_served_back(self):
        # Appending to a real image survives a magic-byte check. It does NOT
        # survive a decode-and-re-encode, which is why we re-encode.
        clean, ct = sanitize_receipt(_png() + b"<script>alert(1)</script>", "image/png")
        assert ct == "image/webp"
        assert b"<script>" not in clean

    def test_a_pdf_that_claims_to_be_a_png_is_still_judged_by_its_bytes(self):
        _clean, ct = sanitize_receipt(_pdf(), "image/png")
        assert ct == "application/pdf"

    def test_a_png_that_claims_to_be_a_pdf_is_still_judged_by_its_bytes(self):
        _clean, ct = sanitize_receipt(_png(), "application/pdf")
        assert ct == "image/webp"

    def test_a_truncated_image_is_refused_rather_than_stored_broken(self):
        _rejected(_png()[:20], "image/png")


# ── resource limits ─────────────────────────────────────────────────────────

class TestResourceLimits:
    def test_a_decompression_bomb_is_refused_before_it_is_decoded(self):
        # 25 000² is 625 megapixels — ~2.5 GB decoded. The header says so
        # before a single row is expanded, which is the only safe moment.
        _rejected(_decompression_bomb(), "image/png")

    def test_our_own_pixel_cap_bites_before_the_decoder_s_default(self):
        """A raster that Pillow would happily decode and we will not.

        Pillow's own `MAX_IMAGE_PIXELS` sits near 89 MP and only WARNS below
        twice that. Our cap is 50 MP, so a 60 MP image is refused here and
        nowhere else — which is the whole point of checking the header
        ourselves rather than relying on a library default that a future
        upgrade may move.
        """
        from app.core.upload_guard import MAX_IMAGE_PIXELS

        # 8000 × 7500 = 60 MP: above our cap, below Pillow's.
        w, h = 8000, 7500
        assert w * h > MAX_IMAGE_PIXELS
        from PIL import Image
        assert w * h < (Image.MAX_IMAGE_PIXELS or 0) * 2

        exc = _rejected(_png_header(w, h), "image/png")
        assert exc.detail == "image_too_many_pixels"

    def test_a_pathological_aspect_ratio_is_refused_by_the_dimension_cap(self):
        # 1 × 400 000 slips under a pixel-count cap while still being absurd.
        from app.core.upload_guard import MAX_IMAGE_DIMENSION

        exc = _rejected(_png_header(MAX_IMAGE_DIMENSION + 1, 1), "image/png")
        assert exc.detail == "image_too_large_dimensions"

    def test_an_oversize_file_is_refused(self):
        from app.core.limits import MAX_UPLOAD_BYTES
        _rejected(b"%PDF-1.4\n" + b"0" * (MAX_UPLOAD_BYTES + 1), "application/pdf")

    def test_a_pdf_with_too_many_pages_is_refused(self):
        # A receipt is a slip, not a book. An unbounded page count is a render
        # and storage amplifier.
        _rejected(_pdf(pages=200), "application/pdf")

    def test_an_ordinary_photo_from_a_phone_still_fits(self):
        # The limits must not reject the actual use case: a 12MP camera photo.
        _clean, ct = sanitize_receipt(_jpeg(4000, 3000), "image/jpeg")
        assert ct == "image/webp"


# ── privacy ─────────────────────────────────────────────────────────────────

class TestMetadataIsStripped:
    def test_exif_including_gps_does_not_survive(self):
        from PIL import Image

        # Reopened from a buffer rather than built in memory: Pillow only
        # populates a writable Exif block on an image that has a backing file.
        plain = io.BytesIO()
        Image.new("RGB", (32, 32), (1, 2, 3)).save(plain, format="JPEG")
        plain.seek(0)
        img = Image.open(plain)
        exif = img.getexif()
        exif[0x010F] = "SecretCameraMaker"             # Make
        exif[0x9286] = "GPSDATA-SECRET-LOCATION"       # UserComment
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif)
        original = buf.getvalue()
        assert b"SecretCameraMaker" in original

        clean, _ct = sanitize_receipt(original, "image/jpeg")
        assert b"SecretCameraMaker" not in clean
        assert b"GPSDATA-SECRET-LOCATION" not in clean
