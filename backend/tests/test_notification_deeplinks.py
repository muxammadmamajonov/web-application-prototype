"""Bot "Open" buttons must launch the Mini App, not a web page.

Pins the start-param grammar shared with the frontend
(frontend/src/lib/telegram/deepLinks.ts): a payment reminder's deep link must be
`https://t.me/<bot>?startapp=pay_<installment_id>`, which the Mini App decodes to
`/me/installments/<id>/pay`. A drift on either side silently breaks one-tap
reminder → pay.
"""
from __future__ import annotations

from app.services.notifications import installment_pay_deeplink, miniapp_deeplink


def test_miniapp_deeplink_is_a_startapp_launch_link() -> None:
    link = miniapp_deeplink("pay_abc-123")
    assert link.startswith("https://t.me/")
    assert link.endswith("?startapp=pay_abc-123")


def test_installment_pay_deeplink_matches_frontend_pay_contract() -> None:
    iid = "a1b2c3d4-0000-1111-2222-333344445555"
    link = installment_pay_deeplink(iid)
    # The frontend maps `pay_<id>` → /me/installments/<id>/pay; keep them in lockstep.
    assert link.endswith(f"?startapp=pay_{iid}")


def test_tenant_installment_deeplink_produces_correct_path() -> None:
    """Requirement 8: tenant_installment_deeplink should resolve to tinst_<id>
    in the Mini App, distinct from installment_pay_deeplink which goes to the
    customer's own pay screen. This builder is used by the owner to view an
    installment from the tenant dashboard."""
    from app.services.notifications import tenant_installment_deeplink

    iid = "b2c3d4e5-1111-2222-3333-444455556666"
    link = tenant_installment_deeplink(iid)
    assert link.startswith("https://t.me/")
    assert link.endswith(f"?startapp=tinst_{iid}")

    # Verify it's DIFFERENT from the pay link
    pay_link = installment_pay_deeplink(iid)
    assert link != pay_link, (
        "tenant_installment_deeplink and installment_pay_deeplink should "
        "produce different paths for the same ID"
    )


def test_billing_deeplink_produces_correct_path() -> None:
    """Requirement 8: billing_deeplink should resolve to the billing screen in
    the Mini App. This builder is used by the owner to access the tenant's
    billing dashboard."""
    from app.services.notifications import billing_deeplink

    link = billing_deeplink()
    assert link.startswith("https://t.me/")
    assert link.endswith("?startapp=billing")
