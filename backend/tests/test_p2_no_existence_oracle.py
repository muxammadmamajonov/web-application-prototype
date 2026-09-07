"""#89 (oracle part) — not-found and no-access return the same 404.

A 403 for "exists but not yours" vs 404 for "doesn't exist" is an existence
oracle (it confirms another tenant/customer's object id exists). Both KYC/PDF
installment access and the customer payment-status poll now return a uniform 404.

DB-free source guards (the no-access path needs a cross-owner row, exercised in
the CI integration tier). The broader response-envelope + 204-delete
standardization is frontend-coupled and tracked as a follow-up.
"""
from __future__ import annotations

import inspect


def test_pdf_installment_access_has_no_403_oracle() -> None:
    from app.api.pdf import _can_access_installment

    src = inspect.getsource(_can_access_installment)
    assert "HTTP_403" not in src                # no exists-but-not-yours 403
    assert "HTTP_404_NOT_FOUND" in src          # uniform not-found


def test_me_payment_status_has_no_403_oracle() -> None:
    import app.api.me as me

    src = inspect.getsource(me)
    assert 'HTTP_403_FORBIDDEN, "no_access"' not in src
