"""Subscription + overage pricing model (Round 39)

Adds per-limit overage config + branch limit + landing flags to plans, a
per-tenant override blob, makes the count limits nullable (NULL = unlimited),
and seeds the five canonical plans (Starter / Business / Pro / Enterprise /
Flex). Legacy plan rows are removed after repointing their tenants to Starter.

Revision ID: r39planoverage
Revises: r38catalogv2colors
Create Date: 2026-05-31 12:00:00+00:00
"""
from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from alembic import op

revision = "r39planoverage"
down_revision = "r38catalogv2colors"
branch_labels = None
depends_on = None

NEW_CODES = ("starter", "business", "pro", "enterprise", "flex")

# Canonical feature keys (union across plans) — the matrix/landing read these.
_BASE = [
    "telegram_bot", "telegram_webapp", "customer_management", "installments",
    "payment_schedule", "reminders", "click", "payme", "cash", "basic_dashboard",
    "basic_reports", "excel_export", "card_detection", "payment_history",
]
_BUSINESS = ["audit_logs", "pdf_export", "better_reports", "employee_roles",
             "staff_activity", "basic_permissions", "support_standard"]
_PRO = ["multi_branch", "advanced_reports", "commission", "pdf_template_upload",
        "risk_monitoring", "advanced_permissions", "branch_analytics",
        "manager_dashboard", "support_high"]
_ENT = ["api_keys", "webhooks", "custom_domain", "white_label",
        "custom_integrations", "account_manager", "sla", "custom_onboarding"]
ALL_FEATURES = _BASE + _BUSINESS + _PRO + _ENT


def _features(*groups: list[str]) -> dict:
    on = set()
    for g in groups:
        on.update(g)
    return {k: (k in on) for k in ALL_FEATURES}


def _ov(price: int, enabled: bool, mode: str) -> dict:
    return {"price": price, "enabled": enabled, "mode": mode}


_HARD_OFF = _ov(0, False, "hard")

PLANS = [
    {
        "code": "starter", "name_uz": "Starter", "name_ru": "Starter",
        "price_monthly": 79000, "sort_order": 10, "is_popular": False, "is_public": True,
        "is_contact_sales": False,
        "max_staff": 1, "max_active_installments": 100, "max_branches": 1,
        "max_custom_fields": 5, "broadcasts_per_month": 100,
        "multi_branch": False, "reports_advanced": False, "commission_management": False,
        "risk_network_access": False, "allow_pdf_template_upload": False,
        "allow_api_keys": False, "allow_outbound_webhooks": False, "allow_custom_domain": False,
        "features": _features(_BASE),
        "overage": {
            "contract": _ov(1000, True, "soft"), "broadcast": _ov(50, True, "soft"),
            "staff": _HARD_OFF, "branch": _HARD_OFF, "custom_field": _HARD_OFF,
        },
    },
    {
        "code": "business", "name_uz": "Business", "name_ru": "Business",
        "price_monthly": 199000, "sort_order": 20, "is_popular": True, "is_public": True,
        "is_contact_sales": False,
        "max_staff": 5, "max_active_installments": 500, "max_branches": 1,
        "max_custom_fields": 20, "broadcasts_per_month": 500,
        "multi_branch": False, "reports_advanced": True, "commission_management": False,
        "risk_network_access": False, "allow_pdf_template_upload": True,
        "allow_api_keys": False, "allow_outbound_webhooks": False, "allow_custom_domain": False,
        "features": _features(_BASE, _BUSINESS),
        "overage": {
            "contract": _ov(700, True, "soft"), "staff": _ov(20000, True, "soft"),
            "custom_field": _ov(2000, True, "soft"), "broadcast": _ov(35, True, "soft"),
            "branch": _HARD_OFF,
        },
    },
    {
        "code": "pro", "name_uz": "Pro", "name_ru": "Pro",
        "price_monthly": 399000, "sort_order": 30, "is_popular": False, "is_public": True,
        "is_contact_sales": False,
        "max_staff": 20, "max_active_installments": 3000, "max_branches": 10,
        "max_custom_fields": 100, "broadcasts_per_month": 2000,
        "multi_branch": True, "reports_advanced": True, "commission_management": True,
        "risk_network_access": True, "allow_pdf_template_upload": True,
        "allow_api_keys": False, "allow_outbound_webhooks": False, "allow_custom_domain": False,
        "features": _features(_BASE, _BUSINESS, _PRO),
        "overage": {
            "contract": _ov(400, True, "soft"), "staff": _ov(15000, True, "soft"),
            "branch": _ov(50000, True, "soft"), "custom_field": _ov(1000, True, "soft"),
            "broadcast": _ov(25, True, "soft"),
        },
    },
    {
        "code": "enterprise", "name_uz": "Enterprise", "name_ru": "Enterprise",
        "price_monthly": 0, "sort_order": 40, "is_popular": False, "is_public": True,
        "is_contact_sales": True,
        "max_staff": None, "max_active_installments": None, "max_branches": None,
        "max_custom_fields": None, "broadcasts_per_month": None,
        "multi_branch": True, "reports_advanced": True, "commission_management": True,
        "risk_network_access": True, "allow_pdf_template_upload": True,
        "allow_api_keys": True, "allow_outbound_webhooks": True, "allow_custom_domain": True,
        "features": _features(_BASE, _BUSINESS, _PRO, _ENT),
        "overage": {},   # configured per-customer
    },
    {
        "code": "flex", "name_uz": "Flex", "name_ru": "Flex",
        "price_monthly": 49000, "sort_order": 50, "is_popular": False, "is_public": True,
        "is_contact_sales": False,
        "max_staff": 1, "max_active_installments": 0, "max_branches": 1,
        "max_custom_fields": 5, "broadcasts_per_month": 100,
        "multi_branch": False, "reports_advanced": False, "commission_management": False,
        "risk_network_access": False, "allow_pdf_template_upload": False,
        "allow_api_keys": False, "allow_outbound_webhooks": False, "allow_custom_domain": False,
        "features": _features(_BASE),
        "overage": {
            "contract": _ov(1500, True, "soft"), "staff": _ov(25000, True, "soft"),
            "broadcast": _ov(50, True, "soft"), "branch": _HARD_OFF, "custom_field": _HARD_OFF,
        },
    },
]


def upgrade() -> None:
    # 1) New columns
    op.add_column("plans", sa.Column("max_branches", sa.Integer(), nullable=True, server_default="1"))
    op.add_column("plans", sa.Column("overage", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("plans", sa.Column("is_popular", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("plans", sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("plans", sa.Column("is_contact_sales", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("tenants", sa.Column("plan_overrides", sa.JSON(), nullable=False, server_default="{}"))

    # Count limits → nullable (NULL = unlimited).
    for col in ("max_custom_fields", "max_staff", "broadcasts_per_month"):
        op.alter_column("plans", col, existing_type=sa.Integer(), nullable=True)

    # 2) Seed / upsert the five plans.
    conn = op.get_bind()
    cols = (
        "id, code, name_uz, name_ru, price_monthly, max_active_installments, max_staff, "
        "max_branches, max_custom_fields, broadcasts_per_month, sms_included_per_month, "
        "bot_mode, allow_custom_domain, allow_pdf_template_upload, allow_api_keys, "
        "allow_outbound_webhooks, commission_management, multi_branch, risk_network_access, "
        "reports_advanced, overage, features, is_active, is_public, is_popular, "
        "is_contact_sales, sort_order"
    )
    insert_sql = sa.text(
        f"INSERT INTO plans ({cols}) VALUES ("
        ":id, :code, :name_uz, :name_ru, :price_monthly, :max_active_installments, :max_staff, "
        ":max_branches, :max_custom_fields, :broadcasts_per_month, 0, "
        "'shared', :allow_custom_domain, :allow_pdf_template_upload, :allow_api_keys, "
        ":allow_outbound_webhooks, :commission_management, :multi_branch, :risk_network_access, "
        ":reports_advanced, CAST(:overage AS json), CAST(:features AS json), true, :is_public, :is_popular, "
        ":is_contact_sales, :sort_order) "
        "ON CONFLICT (code) DO UPDATE SET "
        "name_uz=EXCLUDED.name_uz, name_ru=EXCLUDED.name_ru, price_monthly=EXCLUDED.price_monthly, "
        "max_active_installments=EXCLUDED.max_active_installments, max_staff=EXCLUDED.max_staff, "
        "max_branches=EXCLUDED.max_branches, max_custom_fields=EXCLUDED.max_custom_fields, "
        "broadcasts_per_month=EXCLUDED.broadcasts_per_month, allow_custom_domain=EXCLUDED.allow_custom_domain, "
        "allow_pdf_template_upload=EXCLUDED.allow_pdf_template_upload, allow_api_keys=EXCLUDED.allow_api_keys, "
        "allow_outbound_webhooks=EXCLUDED.allow_outbound_webhooks, commission_management=EXCLUDED.commission_management, "
        "multi_branch=EXCLUDED.multi_branch, risk_network_access=EXCLUDED.risk_network_access, "
        "reports_advanced=EXCLUDED.reports_advanced, overage=EXCLUDED.overage, features=EXCLUDED.features, "
        "is_active=true, is_public=EXCLUDED.is_public, is_popular=EXCLUDED.is_popular, "
        "is_contact_sales=EXCLUDED.is_contact_sales, sort_order=EXCLUDED.sort_order"
    )
    for p in PLANS:
        conn.execute(insert_sql, {
            "id": uuid.uuid4(), **{k: p[k] for k in (
                "code", "name_uz", "name_ru", "price_monthly", "max_active_installments",
                "max_staff", "max_branches", "max_custom_fields", "broadcasts_per_month",
                "allow_custom_domain", "allow_pdf_template_upload", "allow_api_keys",
                "allow_outbound_webhooks", "commission_management", "multi_branch",
                "risk_network_access", "reports_advanced", "is_public", "is_popular",
                "is_contact_sales", "sort_order",
            )},
            "overage": json.dumps(p["overage"]),
            "features": json.dumps(p["features"]),
        })

    # 3) Repoint tenants on legacy/unknown codes to Starter, then drop legacy rows.
    conn.execute(sa.text(
        "UPDATE tenants SET plan_code='starter' "
        "WHERE plan_code NOT IN ('starter','business','pro','enterprise','flex','trial')"
    ))
    conn.execute(sa.text(
        "DELETE FROM plans WHERE code NOT IN ('starter','business','pro','enterprise','flex')"
    ))


def downgrade() -> None:
    op.drop_column("tenants", "plan_overrides")
    for col in ("is_contact_sales", "is_public", "is_popular", "overage", "max_branches"):
        op.drop_column("plans", col)
    # (Leaves the nullable change + seeded rows in place — harmless.)
