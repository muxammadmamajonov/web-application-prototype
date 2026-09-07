"""A PDF is accepted only when it can be PROVED bounded and passive.

The previous guard matched raw byte substrings and returned "unknown page
count" as a pass. Both fail open: a page tree in a compressed object stream is
deflated bytes, and `/Java#53cript` is a legal spelling of `/JavaScript` that no
substring search finds.

Every case below therefore comes in pairs where it matters — the same
capability in an uncompressed object and inside an object stream — because the
whole point of moving to a parser is that the second one is now visible.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.upload_guard import MAX_PDF_PAGES, UploadRejected, validate_pdf
from app.services.media import sanitize_receipt
from tests._pdf_fixtures import (
    bank_receipt_pdf,
    classic_pdf,
    encrypted_pdf,
    malformed_xref_pdf,
    objstm_pdf,
)


def _refused(data: bytes) -> str:
    with pytest.raises(UploadRejected) as ei:
        validate_pdf(data)
    return ei.value.reason


# ── what must still work ────────────────────────────────────────────────────

class TestValidDocumentsStillPass:
    def test_a_classic_xref_pdf_is_accepted(self):
        validate_pdf(classic_pdf())

    def test_an_object_stream_pdf_is_accepted(self):
        # The shape a modern producer emits, and the one the regex could not read.
        validate_pdf(objstm_pdf())

    def test_an_ordinary_bank_receipt_is_accepted(self):
        validate_pdf(bank_receipt_pdf())

    def test_it_survives_the_whole_upload_path(self):
        clean, ct = sanitize_receipt(bank_receipt_pdf(), "application/pdf")
        assert ct == "application/pdf"
        assert clean.startswith(b"%PDF-")

    @pytest.mark.parametrize("pages", [1, 2, MAX_PDF_PAGES])
    def test_up_to_the_page_limit_is_accepted(self, pages):
        validate_pdf(classic_pdf(pages=pages))


# ── page count ──────────────────────────────────────────────────────────────

class TestPageCountIsProved:
    def test_one_page_over_the_limit_is_refused(self):
        assert _refused(classic_pdf(pages=MAX_PDF_PAGES + 1)) == "pdf_too_many_pages"

    def test_the_limit_is_enforced_through_an_object_stream_too(self):
        # The regex found no `/Count` here at all and let the file through.
        assert _refused(objstm_pdf(pages=MAX_PDF_PAGES + 1)) == "pdf_too_many_pages"

    def test_a_page_count_that_cannot_be_determined_is_refused(self):
        # A catalog pointing at a page tree that is not there. Unknown is a
        # refusal, never a pass.
        broken = classic_pdf().replace(b"/Type /Pages /Kids", b"/Type /Pgs /Kids", 1)
        assert _refused(broken) in ("pdf_unparseable", "pdf_no_pages")

    def test_zero_pages_is_refused(self):
        empty = classic_pdf().replace(b"/Kids [3 0 R] /Count 1", b"/Kids [] /Count 0", 1)
        assert _refused(empty) in ("pdf_no_pages", "pdf_unparseable")


# ── structure ───────────────────────────────────────────────────────────────

class TestMalformedStructureIsRefused:
    def test_a_malformed_xref_is_refused(self):
        assert _refused(malformed_xref_pdf()) in ("pdf_unparseable", "pdf_no_pages")

    def test_a_header_over_html_is_refused(self):
        assert _refused(b"%PDF-1.4\n<html><script>alert(1)</script></html>") in (
            "pdf_unparseable", "pdf_no_pages",
        )

    def test_a_header_with_nothing_behind_it_is_refused(self):
        assert _refused(b"%PDF-1.4 minimal") in ("pdf_unparseable", "pdf_no_pages")

    def test_something_that_is_not_a_pdf_at_all_is_refused(self):
        assert _refused(b"just text") == "pdf_bad_header"

    def test_a_truncated_pdf_is_refused(self):
        assert _refused(classic_pdf()[:60]) in ("pdf_unparseable", "pdf_no_pages")


# ── encryption ──────────────────────────────────────────────────────────────

class TestEncryptedDocumentsAreRefused:
    def test_an_encrypted_pdf_is_refused(self):
        # A document we cannot inspect cannot be proved passive.
        assert _refused(encrypted_pdf()) == "pdf_encrypted"

    def test_it_is_refused_through_the_upload_path_too(self):
        with pytest.raises(HTTPException) as ei:
            sanitize_receipt(encrypted_pdf(), "application/pdf")
        assert ei.value.status_code == 400
        assert ei.value.detail == "pdf_encrypted"


# ── active content ──────────────────────────────────────────────────────────

class TestActiveContentIsRefused:
    @pytest.mark.parametrize("extra", [
        "/OpenAction << /S /JavaScript /JS (app.alert\\(1\\)) >>",
        "/OpenAction << /S /Launch /F (calc.exe) >>",
        "/OpenAction << /S /URI /URI (http://evil.example) >>",
        "/OpenAction << /S /GoToR /F (other.pdf) >>",
        "/AA << /O << /S /JavaScript /JS (x) >> >>",
        "/Names << /JavaScript << /Names [(a) 4 0 R] >> >>",
        "/Names << /EmbeddedFiles << /Names [(a) 4 0 R] >> >>",
        "/AcroForm << /XFA [(x) 4 0 R] >>",
    ])
    def test_a_catalog_capability_is_refused(self, extra):
        assert _refused(classic_pdf(catalog_extra=extra)) == "pdf_active_content"

    @pytest.mark.parametrize("extra", [
        "/AA << /O << /S /JavaScript /JS (x) >> >>",
        "/Annots [<< /Subtype /Link /A << /S /URI /URI (http://evil.example) >> >>]",
        "/Annots [<< /Subtype /Link /A << /S /Launch /F (calc.exe) >> >>]",
        "/Annots [<< /Subtype /FileAttachment /FS << /Type /Filespec /F (x) >> >>]",
    ])
    def test_a_page_level_capability_is_refused(self, extra):
        assert _refused(classic_pdf(page_extra=extra)) == "pdf_active_content"

    def test_an_open_action_is_refused_even_with_no_script_inside_it(self):
        """Caught by `/OpenAction` alone.

        Every other OpenAction case here also carries `/JavaScript` in the
        action dictionary, so the inner key catches those regardless. This one
        is a bare "open at this page" destination — refused because the policy
        is to accept only documents provably passive, not to guess which
        automatic behaviours are benign.
        """
        assert _refused(
            classic_pdf(catalog_extra="/OpenAction [3 0 R /Fit]")
        ) == "pdf_active_content"

    def test_an_escaped_name_is_refused(self):
        """`/Java#53cript` is `/JavaScript`. A substring search never sees it;
        a parser decodes the escape before the name is compared."""
        assert _refused(
            classic_pdf(catalog_extra="/OpenAction << /S /Java#53cript /JS (x) >>")
        ) == "pdf_active_content"

    def test_an_escaped_key_is_refused(self):
        assert _refused(
            classic_pdf(catalog_extra="/Open#41ction << /S /JavaScript /JS (x) >>")
        ) == "pdf_active_content"

    def test_a_capability_inside_a_compressed_object_stream_is_refused(self):
        """THE case the regex could not reach: the catalog is deflated, so the
        bytes `/OpenAction` do not appear anywhere in the file."""
        data = objstm_pdf(catalog_extra="/OpenAction << /S /JavaScript /JS (x) >>")
        assert b"/OpenAction" not in data, "fixture is not actually compressed"
        assert _refused(data) == "pdf_active_content"

    def test_an_embedded_attachment_is_refused(self):
        data = objstm_pdf(
            catalog_extra="/Names << /EmbeddedFiles << /Names [(a) 9 0 R] >> >>"
        )
        assert _refused(data) == "pdf_active_content"


# ── traversal is bounded ────────────────────────────────────────────────────

class TestTraversalIsBounded:
    def test_a_reference_cycle_terminates(self):
        # Two objects pointing at each other. Without a visited set this hangs.
        data = classic_pdf(catalog_extra="/Sib 3 0 R")
        validate_pdf(data.replace(
            b"<< /Type /Page /Parent 2 0 R", b"<< /Type /Page /Parent 1 0 R", 1,
        ))

    def test_the_object_budget_is_finite(self):
        from app.core.upload_guard import MAX_PDF_DEPTH, MAX_PDF_OBJECTS

        assert 0 < MAX_PDF_OBJECTS <= 100_000
        assert 0 < MAX_PDF_DEPTH <= 200
