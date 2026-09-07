"""TR-004 — the tenant API's route contract, pinned in registration order.

``app/api/tenant`` was split from one module into a package. The split must not
move, rename, retype **or reorder** anything a client can observe, so this test
freezes the contract as a literal ordered sequence and compares it to what
FastAPI actually registers, index by index. It is deliberately not a
self-updating snapshot and it deliberately does not sort: order is part of the
contract, because registration order is what decides which handler answers a
request when two patterns could both match.

Each domain module owns a contiguous run of this sequence, which is what lets
five files reproduce it exactly rather than approximately.

The final test records the (currently empty) set of ambiguous route pairs. That
is supporting evidence about how much the order *matters* today — not licence to
stop pinning it.
"""
from __future__ import annotations

import itertools

from fastapi.routing import APIRoute

#: Every route under /api/tenant, in registration order, as
#: (method, path, name, status_code, response_model, tags, dependency count,
#:  response_model_by_alias, operation_id, deprecated).
TENANT_ROUTE_SEQUENCE: tuple[tuple, ...] = (
    ('POST', '/api/tenant/invitations', 'create_invitation', 201, "<class 'app.api.invitations.InvitationOut'>", ('invitations',), 1, True, None, None),
    ('GET', '/api/tenant/invitations', 'list_invitations', None, 'list[app.api.invitations.InvitationOut]', ('invitations',), 0, True, None, None),
    ('DELETE', '/api/tenant/invitations/{invite_id}', 'revoke_invitation', 204, None, ('invitations',), 0, True, None, None),
    ('GET', '/api/tenant/installments/{installment_id}/contract.pdf', 'installment_contract_pdf', None, None, ('pdf',), 1, True, None, None),
    ('GET', '/api/tenant/payments/{payment_id}/receipt.pdf', 'payment_receipt_pdf', None, None, ('pdf',), 1, True, None, None),
    ('GET', '/api/tenant/invoices/{invoice_id}/invoice.pdf', 'tenant_invoice_pdf', None, None, ('pdf',), 1, True, None, None),
    ('PUT', '/api/tenant/settings/contract-template', 'update_contract_template', None, None, ('pdf',), 0, True, None, None),
    ('DELETE', '/api/tenant/settings/contract-template', 'delete_contract_template', 204, None, ('pdf',), 0, True, None, None),
    ('GET', '/api/tenant/settings/contract-template/preview.pdf', 'preview_contract_template', None, None, ('pdf',), 1, True, None, None),
    ('GET', '/api/tenant/reports/aging', 'report_aging', None, None, ('reports',), 1, True, None, None),
    ('GET', '/api/tenant/reports/cashflow', 'report_cashflow', None, None, ('reports',), 1, True, None, None),
    ('GET', '/api/tenant/reports/staff-performance', 'report_staff', None, None, ('reports',), 1, True, None, None),
    ('GET', '/api/tenant/reports/products', 'report_products', None, None, ('reports',), 1, True, None, None),
    ('GET', '/api/tenant/reports/ltv', 'report_ltv', None, None, ('reports',), 1, True, None, None),
    ('GET', '/api/tenant/reports/margin', 'report_margin', None, None, ('reports',), 1, True, None, None),
    ('GET', '/api/tenant/sms/usage', 'sms_usage', None, "<class 'dict'>", ('sms',), 0, True, None, None),
    ('GET', '/api/tenant/sms/history', 'sms_history', None, 'list[dict]', ('sms',), 0, True, None, None),
    ('GET', '/api/tenant/customers/{cid}/channels', 'list_customer_channels', None, 'list[dict]', ('sms',), 0, True, None, None),
    ('PUT', '/api/tenant/customers/{cid}/channels', 'set_customer_channels', None, 'list[dict]', ('sms',), 0, True, None, None),
    ('GET', '/api/tenant/overview', 'tenant_overview', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('GET', '/api/tenant/kyc-media', 'kyc_media', None, None, ('tenant',), 0, True, None, None),
    ('GET', '/api/tenant/customers', 'list_customers', None, "<class 'dict'>", ('tenant',), 1, True, None, None),
    ('POST', '/api/tenant/customers/{cid}/approve', 'approve_customer', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('POST', '/api/tenant/customers/{cid}/reject', 'reject_customer', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('GET', '/api/tenant/customers/{cid}', 'get_customer', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('POST', '/api/tenant/customers', 'create_customer', 201, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('POST', '/api/tenant/customers/{cid}/invite', 'resend_customer_invite', 201, "<class 'dict'>", ('tenant',), 1, True, None, None),
    ('PATCH', '/api/tenant/customers/{cid}', 'update_customer', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('DELETE', '/api/tenant/customers/{cid}', 'delete_customer', 204, None, ('tenant',), 0, True, None, None),
    ('GET', '/api/tenant/customers/{cid}/export', 'export_customer_data', None, "<class 'dict'>", ('tenant',), 1, True, None, None),
    ('POST', '/api/tenant/customers/{cid}/erase', 'erase_customer_data', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('GET', '/api/tenant/guarantors', 'list_guarantors', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('POST', '/api/tenant/guarantors', 'create_guarantor', 201, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('PATCH', '/api/tenant/guarantors/{gid}', 'update_guarantor', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('DELETE', '/api/tenant/guarantors/{gid}', 'delete_guarantor', 204, None, ('tenant',), 0, True, None, None),
    ('GET', '/api/tenant/imei-check', 'imei_check', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('GET', '/api/tenant/risk/score', 'risk_score', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('POST', '/api/tenant/risk/report', 'risk_report', 201, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('GET', '/api/tenant/products', 'list_products', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('GET', '/api/tenant/products/{pid}', 'get_product', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('POST', '/api/tenant/products', 'create_product', 201, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('PATCH', '/api/tenant/products/{pid}', 'update_product', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('POST', '/api/tenant/upload-image', 'tenant_upload_image', None, "<class 'dict'>", ('tenant',), 1, True, None, None),
    ('POST', '/api/tenant/upload-kyc', 'tenant_upload_kyc', None, "<class 'dict'>", ('tenant',), 1, True, None, None),
    ('DELETE', '/api/tenant/products/{pid}', 'delete_product', 204, None, ('tenant',), 0, True, None, None),
    ('GET', '/api/tenant/installments', 'list_installments', None, "<class 'dict'>", ('tenant',), 1, True, None, None),
    ('GET', '/api/tenant/worklist', 'collections_worklist', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('POST', '/api/tenant/installments/{iid}/remind', 'remind_installment', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('GET', '/api/tenant/installments/{iid}', 'get_installment', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('POST', '/api/tenant/installments', 'create_installment', 201, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('POST', '/api/tenant/installments/{iid}/cancel', 'cancel_installment', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('POST', '/api/tenant/installments/{iid}/reschedule', 'reschedule_installment', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('PUT', '/api/tenant/installments/{iid}', 'replace_installment', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('GET', '/api/tenant/payments', 'list_payments', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('GET', '/api/tenant/staff-today', 'staff_today', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('POST', '/api/tenant/payments', 'create_payment', 201, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('GET', '/api/tenant/payments/{payment_id}/review', 'payment_review', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('POST', '/api/tenant/payments/{payment_id}/approve', 'approve_payment', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('POST', '/api/tenant/payments/{payment_id}/reject', 'reject_payment', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('GET', '/api/tenant/billing', 'tenant_billing', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('GET', '/api/tenant/invoices', 'tenant_invoices', None, 'list[dict]', ('tenant',), 0, True, None, None),
    ('POST', '/api/tenant/plan-change', 'tenant_plan_change', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('GET', '/api/tenant/billing/topup-info', 'tenant_topup_info', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('GET', '/api/tenant/billing/topups', 'tenant_topups', None, 'list[dict]', ('tenant',), 0, True, None, None),
    ('POST', '/api/tenant/billing/topup', 'tenant_create_topup', 201, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('GET', '/api/tenant/products/{pid}/movements', 'product_movements', None, 'list[dict]', ('tenant',), 0, True, None, None),
    ('POST', '/api/tenant/products/{pid}/stock-adjust', 'adjust_stock', 201, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('GET', '/api/tenant/inventory/low-stock', 'low_stock', None, 'list[dict]', ('tenant',), 0, True, None, None),
    ('POST', '/api/tenant/import/preview', 'import_preview', None, "<class 'dict'>", ('tenant',), 1, True, None, None),
    ('POST', '/api/tenant/products/import', 'import_products', None, "<class 'dict'>", ('tenant',), 1, True, None, None),
    ('POST', '/api/tenant/customers/import', 'import_customers', None, "<class 'dict'>", ('tenant',), 1, True, None, None),
    ('GET', '/api/tenant/broadcasts', 'list_broadcasts', None, "<class 'dict'>", ('tenant',), 0, True, None, None),
    ('POST', '/api/tenant/broadcasts', 'create_broadcast', 201, "<class 'dict'>", ('tenant',), 1, True, None, None),
    ('DELETE', '/api/tenant/broadcasts/{bid}', 'delete_broadcast', 204, None, ('tenant',), 0, True, None, None),
    ('GET', '/api/tenant/analytics/summary', 'analytics_summary', None, None, ('tenant', 'analytics'), 1, True, None, None),
    ('GET', '/api/tenant/analytics/sales-by-category', 'analytics_sales_by_category', None, None, ('tenant', 'analytics'), 1, True, None, None),
    ('GET', '/api/tenant/settings', 'get_settings', None, "<class 'dict'>", ('tenant.settings',), 0, True, None, None),
    ('PATCH', '/api/tenant/settings', 'patch_settings', None, "<class 'dict'>", ('tenant.settings',), 0, True, None, None),
    ('POST', '/api/tenant/settings/bot/activate', 'activate_tenant_bot', None, "<class 'dict'>", ('tenant.settings',), 0, True, None, None),
    ('POST', '/api/tenant/settings/bot/deactivate', 'deactivate_tenant_bot', None, "<class 'dict'>", ('tenant.settings',), 0, True, None, None),
    ('GET', '/api/tenant/staff', 'list_staff', None, 'list[app.api.tenant_settings.StaffRow]', ('tenant.settings',), 0, True, None, None),
    ('PATCH', '/api/tenant/staff/{tu_id}', 'update_staff', None, "<class 'dict'>", ('tenant.settings',), 0, True, None, None),
    ('DELETE', '/api/tenant/staff/{tu_id}', 'revoke_staff', 204, None, ('tenant.settings',), 0, True, None, None),
    ('POST', '/api/tenant/settings/reminders/test', 'test_reminder_send', None, None, ('tenant.settings',), 0, True, None, None),
    ('GET', '/api/tenant/settings/holidays', 'list_tenant_holidays', None, 'list[dict]', ('tenant.settings',), 0, True, None, None),
)


def _tenant_routes() -> list[APIRoute]:
    from app.main import app
    return [
        r for r in app.routes
        if isinstance(r, APIRoute) and r.path.startswith("/api/tenant")
    ]


def _row(r: APIRoute) -> tuple:
    return (
        sorted(r.methods)[0], r.path, r.name, r.status_code,
        repr(r.response_model) if r.response_model else None,
        tuple(r.tags or []), len(r.dependencies or []),
        r.response_model_by_alias, r.operation_id, r.deprecated,
    )


def test_tenant_routes_are_registered_in_the_expected_order() -> None:
    """The whole /api/tenant contract, index by index. No sorting, no sets."""
    live = [_row(r) for r in _tenant_routes()]

    assert len(live) == len(TENANT_ROUTE_SEQUENCE), (
        f"route count changed: {len(TENANT_ROUTE_SEQUENCE)} -> {len(live)}"
    )
    for i, (expected, actual) in enumerate(zip(TENANT_ROUTE_SEQUENCE, live, strict=True)):
        assert actual == expected, (
            f"route {i} changed:\n  expected {expected}\n  actual   {actual}"
        )
    assert live == list(TENANT_ROUTE_SEQUENCE)


def test_no_tenant_route_is_registered_twice() -> None:
    live = [(r[0], r[1]) for r in (_row(x) for x in _tenant_routes())]
    assert len(live) == len(set(live)), "a duplicate (method, path) appeared"


def test_ambiguous_route_pairs_are_recorded() -> None:
    """How much the pinned order currently matters.

    Two routes sharing a method whose patterns can both match one concrete path
    would make registration order decide the winner. None exist today; the order
    above is pinned regardless, so this staying empty is an observation rather
    than a licence.
    """
    routes = _tenant_routes()

    def segs(p: str) -> list[str]:
        return p.strip("/").split("/")

    def overlaps(a: str, b: str) -> bool:
        A, B = segs(a), segs(b)
        if len(A) != len(B):
            return False
        return all(
            x == y or (x.startswith("{") and x.endswith("}")) or (y.startswith("{") and y.endswith("}"))
            for x, y in zip(A, B, strict=True)
        )

    clashes = [
        (r1.path, r2.path)
        for r1, r2 in itertools.combinations(routes, 2)
        if (r1.methods & r2.methods) and overlaps(r1.path, r2.path)
    ]
    assert clashes == []
