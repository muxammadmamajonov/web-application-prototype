"""Round 19 — PDF render smoke tests.

Exercises the pure parts of `app.services.pdf`:
  * validator rejects forbidden Jinja constructs
  * validator accepts allowlisted use
  * template files exist and parse

Full render (which hits the DB) is verified manually after deploy by
hitting `/api/tenant/installments/{id}/contract.pdf`.
"""
from __future__ import annotations

import pytest

from app.services.pdf import (
    ALLOWED_VARIABLES,
    TemplateValidationError,
    _env,
    validate_custom_template,
)


def test_allowed_variables_set_is_sane() -> None:
    expected = {
        "tenant", "customer", "installment", "items",
        "schedule", "guarantors", "today", "totals", "settings_block",
    }
    assert expected == ALLOWED_VARIABLES


def test_validator_passes_minimal_safe_template() -> None:
    validate_custom_template(
        "<!doctype html><html><body><h1>{{ tenant.name }}</h1>"
        "{% for it in items %}<p>{{ it.name }}</p>{% endfor %}</body></html>"
    )


@pytest.mark.parametrize(
    "bad",
    [
        "{% include 'whatever.html' %}",
        "{% extends 'base.html' %}",
        "{% from 'mod' import x %}",
        "{% import 'mod' as m %}",
        "{% macro foo() %}{% endmacro %}",
        "{{ config.secret_key }}",
        "{{ request.headers }}",
        "{{ self.__class__ }}",
        "{{ ''.__class__.__mro__ }}",
    ],
)
def test_validator_rejects_dangerous_constructs(bad: str) -> None:
    with pytest.raises(TemplateValidationError):
        validate_custom_template(bad)


def test_validator_rejects_empty() -> None:
    with pytest.raises(TemplateValidationError, match="template_empty"):
        validate_custom_template("")
    with pytest.raises(TemplateValidationError, match="template_empty"):
        validate_custom_template("   \n\n  ")


def test_validator_rejects_oversize() -> None:
    huge = "<p>x</p>" * 30_000  # ~240 KB
    with pytest.raises(TemplateValidationError, match="template_too_large"):
        validate_custom_template(huge)


def test_default_templates_load() -> None:
    """Both bundled templates parse without Jinja syntax errors."""
    _env.get_template("contracts/default_uz.html")
    _env.get_template("receipts/default.html")


def test_default_contract_template_renders_with_full_context() -> None:
    """The default contract template must RENDER (not just parse) against the
    context shape that ``_load_installment_context`` / ``render_contract_preview``
    produce. Guards against template↔context drift under StrictUndefined — e.g.
    the template referenced ``installment.late_fee_pct_per_day`` which the context
    never provided, so every contract PDF raised UndefinedError → 500. The
    parse-only ``test_default_templates_load`` could not catch this.
    """
    from datetime import date, timedelta
    from decimal import Decimal

    today = date.today()
    months = 6
    monthly = Decimal("500000")
    total = monthly * months
    ctx = {
        "tenant": {"name": "Namuna Do'kon", "slug": "namuna", "logo_url": None,
                   "address": "Toshkent", "phone": "+998901234567", "email": None},
        "customer": {"full_name": "Aliyev Vali", "phone": "+998901234567",
                     "passport_id": "AA1234567", "passport_series": "AA",
                     "passport_number": "1234567", "jshshir": "30112900010001",
                     "birthdate": "1990-01-01", "address": "Toshkent"},
        "installment": {"id": "x", "reference": "NAMUNA-1", "contract_number": "MDD-NAMUNA",
                        "start_date": today.isoformat(), "months": months, "interest_rate": None,
                        "monthly_amount": str(monthly), "total_amount": str(total),
                        "down_payment": "0", "principal": str(total), "calc_formula": None,
                        "late_fee_pct_per_day": None},
        "items": [{"name": "Namuna", "variant": None, "serial": None, "qty": 1,
                   "unit_price": str(total), "line_total": str(total)}],
        "schedule": [{"n": i, "due_date": (today + timedelta(days=30 * i)).isoformat(),
                      "amount": str(monthly)} for i in range(1, months + 1)],
        # Exercise the guarantor loop (references g.full_name/passport_id/phone).
        "guarantors": [{"full_name": "Kafil Aka", "passport_id": "BB7654321", "phone": "+998900000000"}],
        "today": today.isoformat(),
        "totals": {"principal": str(total), "down_payment": "0", "total_amount": str(total),
                   "monthly": str(monthly), "months": months},
        # `currency_label` is what the template prints; `currency` stays for
        # custom tenant templates that reference the code itself.
        "settings_block": {
            "business_name": "Namuna Do'kon", "receipt_footer": None,
            "currency": "UZS", "currency_label": "so'm",
        },
    }
    html = _env.get_template("contracts/default_uz.html").render(**ctx)
    assert "Namuna" in html and len(html) > 500
