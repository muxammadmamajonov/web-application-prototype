"""Shared Pydantic field validators (#136)."""
from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator


def _uz_phone(v: str | None) -> str | None:
    """Reject a phone that isn't a valid Uzbek number, and CANONICALISE it to
    ``+998XXXXXXXXX``. None/empty pass through — the field's own required-ness is
    enforced separately. Accepts +998…, 998… and bare 9-digit local forms, so
    only genuine garbage is rejected (low signup/claim-funnel risk).

    This used to preserve the caller's formatting on the reasoning that we
    validate rather than rewrite. That was the bug: uniqueness is enforced by
    `uq_customer_tenant_phone`, a plain constraint on the raw column, so the
    same person could be entered as ``+998 90 123 45 67`` and again as
    ``998901234567`` in one shop. Digit-substring search had the same hole — a
    query for ``901234567`` could not match a stored value containing spaces.
    One stored form fixes both, and makes "does this person already exist?" an
    exact match instead of a fuzzy one."""
    if v is None or not str(v).strip():
        return v
    from app.services.sms_eskiz import canonical_uz_phone
    canonical = canonical_uz_phone(v)
    if canonical is None:
        raise ValueError("phone must be a valid Uzbek number, e.g. +998901234567")
    return canonical


#: Required UZ phone (still combine with Field(min_length=…) as needed).
UzPhone = Annotated[str, AfterValidator(_uz_phone)]
#: Optional UZ phone.
UzPhoneOpt = Annotated[str | None, AfterValidator(_uz_phone)]


#: `local@domain.tld`, no whitespace, TLD of at least two characters.
#:
#: Deliberately byte-identical to `isValidEmail` in `frontend/src/lib/email.ts`,
#: which is the gate the visitor actually sees. A stricter grammar here would
#: reject an address the form has already told them is fine — that mismatch is
#: precisely the defect this type exists to close, so the two are pinned
#: together by `frontend/src/lib/leadContact.test.ts`.
EMAIL_PATTERN = r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$"
_EMAIL = re.compile(EMAIL_PATTERN)


def _uz_phone_or_email(v: str) -> str:
    """A public lead's contact point: one valid UZ phone *or* one valid email.

    The public forms accept either, because a shop owner may prefer to be reached
    on either. Validating (never rewriting) keeps whatever the visitor typed, so
    the salesperson dials or writes to the string they actually gave.

    This stays a closed set of two grammars on purpose. Widening it to free text
    would turn a contact field into an unbounded note and leave the follow-up
    with nothing to act on.
    """
    text = str(v).strip()
    if _EMAIL.match(text):
        return v
    from app.services.sms_eskiz import normalize_uz_phone
    if normalize_uz_phone(text) is not None:
        return v
    raise ValueError(
        "contact must be a valid Uzbek phone number (e.g. +998901234567) "
        "or an email address"
    )


#: Required contact point for a public lead — UZ phone or email, nothing else.
UzPhoneOrEmail = Annotated[str, AfterValidator(_uz_phone_or_email)]


# ── logo URL (#14) ───────────────────────────────────────────────────────────

# Mirrors frontend/src/lib/logoUrl.ts; `logoUrl.contract.test.ts` pins the two.
LOGO_URL_MIN = 11          # `https://a.b` is already 11 characters
LOGO_URL_MAX = 512         # past any real CDN URL, short of bloating a PDF


def validate_logo_url(raw: str | None) -> str | None:
    """Return the cleaned URL, or raise ValueError naming what is wrong.

    HTTPS only: the app is served over HTTPS, so an ``http://`` logo is blocked
    as mixed content by every browser this renders in — storing one would
    guarantee a broken image. Other schemes (``javascript:``, ``data:``,
    ``file:``) are refused outright rather than sanitised downstream, because
    this value is interpolated into contract PDFs and shop pages.

    The field is optional; empty input is valid and normalises to ``None``.
    """
    from urllib.parse import urlparse

    v = (raw or "").strip()
    if not v:
        return None
    if len(v) < LOGO_URL_MIN:
        raise ValueError("logo_url_too_short")
    if len(v) > LOGO_URL_MAX:
        raise ValueError("logo_url_too_long")
    parsed = urlparse(v)
    if parsed.scheme != "https":
        raise ValueError("logo_url_bad_scheme")
    if not parsed.hostname or "." not in parsed.hostname:
        raise ValueError("logo_url_malformed")
    return v
