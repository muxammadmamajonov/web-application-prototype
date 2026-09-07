"""PDF generation — contracts + receipts via WeasyPrint.

Why WeasyPrint
--------------
* Pure HTML + CSS input → pixel-perfect PDF output. No Headless Chrome,
  no LaTeX. Renders Cyrillic + Lotin scripts correctly when DejaVu / Noto
  fonts are installed (see Dockerfile).
* Streaming `bytes` API fits cleanly into a FastAPI StreamingResponse;
  no temp files, no disk hits in prod.

Two render paths
----------------
* `render_contract_pdf(installment_id)` — uses the tenant's custom HTML
  (Pro+ plan, stored in `tenant_settings.contract_template_html`) if
  present, otherwise the bundled `templates/contracts/default_uz.html`.
* `render_receipt_pdf(payment_id)` — single thermal-printable layout
  (80mm width) bundled at `templates/receipts/default.html`. Tenants don't
  customise receipts in this round; Round 28+ revisit.

Placeholder whitelist
---------------------
Custom HTML uploads MUST only reference the allowlisted variables. The
validator runs on every save so a hostile template can't `{{config}}`-leak
a Click secret. Jinja's autoescape is on by default.
"""
from __future__ import annotations

import asyncio
import logging
import re
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.currency import currency_for_new_contract
from app.core.documents import currency_label, long_date
from app.core.pii_policy import (
    CONTRACT_GUARANTOR_PII_FIELDS,
    CONTRACT_PII_FIELDS,
    mask_pii_fields,
)
from app.models import (
    Customer,
    Installment,
    InstallmentItem,
    Payment,
    Tenant,
    TenantSettings,
)

log = logging.getLogger("muddato.pdf")

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"


def _profile_ctx(tenant: Tenant, settings_row: TenantSettings | None) -> dict:
    """The shop block for a document template, from the one shared resolver.

    Kept as the template's existing key names so the Jinja templates (and any
    tenant-uploaded custom template, which we do not control) keep working
    unchanged — the fix is where the values come FROM, not what they are called.
    """
    from app.services.tenant_profile import resolve

    p = resolve(tenant, settings_row)
    return {
        "name": p.name,
        "slug": p.slug,
        "logo_url": p.logo_url,
        "address": p.address,
        "phone": p.phone,
        "email": p.email,
    }


def _safe_pdf_url_fetcher(url: str, *args, **kwargs):
    """(audit/sec) SSRF + local-file-read guard for WeasyPrint.

    The default fetcher would resolve a tenant-controlled ``<img src="file:///etc/passwd">``
    or ``url(http://169.254.169.254/...)`` at render time (logo_url on the default
    template; arbitrary HTML/CSS on Pro+ custom templates). This allows only:
    ``data:`` URIs, local files INSIDE the bundled templates dir (fonts/CSS that
    ``base_url`` resolves), and http(s) to PUBLIC hosts (real tenant logos on
    object storage). It blocks ``file://`` outside the templates dir and any
    private / loopback / link-local / reserved address (cloud metadata + internal).
    """
    import urllib.parse

    from weasyprint import default_url_fetcher

    from app.core.urlsafety import UnsafeUrlError, assert_public_host

    parsed = urllib.parse.urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme == "data":
        return default_url_fetcher(url, *args, **kwargs)
    if scheme in ("", "file"):
        path = Path(urllib.parse.unquote(parsed.path or url)).resolve()
        if path == TEMPLATES_DIR or TEMPLATES_DIR in path.parents:
            return default_url_fetcher(url, *args, **kwargs)
        raise ValueError("pdf: blocked local file outside templates dir")
    if scheme in ("http", "https"):
        host = parsed.hostname
        if not host:
            raise ValueError("pdf: blocked url with no host")
        # Shared address predicate (app/core/urlsafety.py) — the KYC media proxy
        # applies the same one, so a hardening here reaches both fetchers.
        try:
            assert_public_host(host)
        except UnsafeUrlError as exc:
            raise ValueError(f"pdf: {exc}") from exc
        return default_url_fetcher(url, *args, **kwargs)
    raise ValueError(f"pdf: blocked url scheme {scheme!r}")

# Single shared env. StrictUndefined surfaces missing placeholders during
# preview instead of silently rendering "" — the tenant fixes the template
# before customers see it.
_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "htm", "xml"]),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)

# Tenant-supplied templates are compiled HERE, never in `_env` above.
#
# THE VULNERABILITY THIS CLOSES (audit, 2026-08-09): a tenant owner/manager on a
# plan with `allow_pdf_template_upload` can save custom contract HTML, and it was
# compiled with the ordinary Environment — a full-power Jinja engine — guarded
# only by the regex blocklist in `validate_custom_template`. That blocklist is
# defeated by string concatenation, because its dunder rule `__\w+__` only
# matches contiguous tokens:
#
#     {{ (cycler|attr('__ini'~'t__')|attr('__glob'~'als__'))['o'~'s'] }}
#
# passes validation and reaches `os`. That is arbitrary code execution inside the
# API process: it yields `settings.secret_key` (forge any user's session), the
# database URL, and the key that decrypts every tenant's payment credentials —
# and it runs as the `postgres` connection role, which bypasses RLS entirely. One
# tenant could read and rewrite every other tenant's ledger. Confirmed executed,
# not theoretical.
#
# A blocklist cannot win this: the attacker picks the expression and Jinja's
# object graph offers many routes to the same place. SandboxedEnvironment inverts
# it — attribute access is mediated, so the traversal is blocked at the engine
# rather than at a pattern. The regex validator is retained as a cheap first
# filter and for its clear error messages, but it is no longer what stands
# between a tenant and the process.
#
# Deliberately a SECOND environment: the built-in templates under TEMPLATES_DIR
# are ours and trusted, and sandboxing those would risk breaking legitimate
# rendering for no security gain. Untrusted input gets the sandbox; our own
# templates keep the plain environment.
_untrusted_env = SandboxedEnvironment(
    autoescape=select_autoescape(["html", "htm", "xml"]),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


# ── placeholder whitelist for custom uploads ──────────────────────────────

# Custom templates can only reference these top-level paths. Anything else
# raises during validation so a tenant can't `{{config.secret_key}}`.
ALLOWED_VARIABLES = {
    "tenant", "customer", "installment", "items", "schedule",
    "guarantors", "today", "totals", "settings_block",
}

# Block these Jinja constructs in custom uploads — they enable code execution
# (eval), arbitrary attribute access, or template inheritance side-channels.
FORBIDDEN_PATTERNS = [
    re.compile(r"{%\s*import\s"),
    re.compile(r"{%\s*include\s"),
    re.compile(r"{%\s*extends\s"),
    re.compile(r"{%\s*from\s"),
    re.compile(r"{%\s*macro\s"),
    re.compile(r"__\w+__"),  # dunder access
    re.compile(r"\bconfig\b"),
    re.compile(r"\brequest\b"),
    re.compile(r"\bself\b"),
]


class TemplateValidationError(ValueError):
    """Raised when a tenant-uploaded HTML template fails the safety check."""


def validate_custom_template(html: str) -> None:
    """Reject custom uploads with forbidden Jinja constructs.

    Lightweight (regex-based) — defence in depth, not a full sandbox. The
    actual render call uses `select_autoescape` + StrictUndefined as a
    second layer, and WeasyPrint itself never touches the network.
    """
    if not html or not html.strip():
        raise TemplateValidationError("template_empty")
    if len(html) > 200_000:
        raise TemplateValidationError("template_too_large")
    for pat in FORBIDDEN_PATTERNS:
        if pat.search(html):
            raise TemplateValidationError(f"forbidden_pattern: {pat.pattern}")


# ── data shaping ──────────────────────────────────────────────────────────

def _fmt_for(currency: str | None, locale: str):
    """A one-argument money formatter bound to this document's currency.

    Bound rather than passed around so no template line can accidentally format
    one figure in the contract's currency and the next in the shop's."""
    from app.core.documents import money

    return lambda v: money(v, currency)


async def _load_installment_context(
    db: AsyncSession, installment_id: UUID, *, locale: str = "uz",
    mask_pii: bool = False,
) -> dict:
    """Pull everything a contract template might need into one big dict.

    Loaded eagerly so the template can render without async DB hops (Jinja
    is sync). Each section is namespaced under a top-level key matching
    `ALLOWED_VARIABLES`.
    """
    inst = (await db.execute(
        select(Installment).where(Installment.id == installment_id)
    )).scalar_one_or_none()
    if inst is None:
        raise ValueError(f"installment_not_found: {installment_id}")

    tenant = (await db.execute(select(Tenant).where(Tenant.id == inst.tenant_id))).scalar_one()
    customer = (await db.execute(select(Customer).where(Customer.id == inst.customer_id))).scalar_one()
    settings_row = (await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
    )).scalar_one_or_none()
    items = (await db.execute(
        select(InstallmentItem).where(InstallmentItem.installment_id == inst.id)
    )).scalars().all()

    # Synthesize the payment schedule from `start_date`, `months`, `monthly_amount`.
    # Real-world tenants may want an actual schedule table later; for now
    # this matches what the wizard implies.
    from app.services.installments import add_months

    schedule = []
    for i in range(1, (inst.months or 0) + 1):
        due = add_months(inst.start_date, i)
        schedule.append({
            "n": i,
            "due_date": long_date(due, locale),
            "amount": _fmt_for(inst.currency, locale)(inst.monthly_amount or Decimal("0")),
        })

    # Every money and date value a document shows is formatted HERE, through the
    # one module that decides how the platform writes a number (#B4). Templates
    # never see a raw `Decimal` or an ISO string, which is what produced
    # `6000000.00` on one document and `6 000 000` on another.
    cur = inst.currency
    fmt = _fmt_for(cur, locale)
    totals = {
        "principal":    fmt(inst.principal),
        "down_payment": fmt(inst.down_payment),
        "total_amount": fmt(inst.total_amount),
        "monthly":      fmt(inst.monthly_amount),
        "months":       inst.months,
        # What is still owed, as opposed to what was borrowed. The receipt
        # printed the second under a label meaning the first.
        "remaining":    fmt(getattr(inst, "remaining_balance", None)),
    }

    from datetime import date as _date

    #: Placeholder for the guarantor rows Round 22 will load. Declared here, and
    #: typed, so the masking comprehension below has something real to map over.
    guarantor_rows: list[dict] = []

    return {
        # The shop's profile, resolved in ONE place (services/tenant_profile).
        # This rendered `tenant.name` — the row the platform stamped at
        # provisioning — so a shop that renamed itself in Settings kept the old
        # name on every contract it generated. On a legal document.
        "tenant": _profile_ctx(tenant, settings_row),
        # (MUDD-139) The identity-document fields below are the same ones
        # `_customer_dict` masks for a `sales` member. `mask_pii` is decided by
        # `app.core.pii_policy.contract_pii_masked_for` from the CALLER, which is
        # why it arrives as an argument rather than being read here — this
        # function has no user. Masking at context-build time (not in the
        # template) means a tenant's own custom contract template inherits it
        # too, instead of re-opening the hole by printing `{{customer.jshshir}}`.
        "customer": mask_pii_fields({
            "full_name":       customer.full_name,
            "phone":           customer.phone,
            "passport_id":     customer.passport_id,
            "passport_series": customer.passport_series,
            "passport_number": customer.passport_number,
            "jshshir":         customer.jshshir,
            "birthdate":       long_date(customer.birthdate, locale) if customer.birthdate else None,
            "address":         customer.address,
        }, CONTRACT_PII_FIELDS if mask_pii else ()),
        "installment": {
            "id":              str(inst.id),
            "reference":       inst.reference,
            "contract_number": inst.contract_number or f"MDD-{inst.created_at.year if inst.created_at else _date.today().year}-{str(inst.id)[:8].upper()}",
            "start_date":      long_date(inst.start_date, locale),
            "months":          inst.months,
            "interest_rate":   str(inst.interest_rate) if inst.interest_rate else None,
            "monthly_amount":  fmt(inst.monthly_amount),
            "total_amount":    fmt(inst.total_amount),
            "down_payment":    fmt(inst.down_payment),
            "principal":       fmt(inst.principal),
            "calc_formula":    inst.calc_formula,
            # The default contract template references this optional per-day late-fee
            # rate; None renders the "tenant-default stavka" fallback. Missing it made
            # the template raise StrictUndefined → 500 on EVERY contract render.
            "late_fee_pct_per_day": getattr(inst, "late_fee_pct_per_day", None),
        },
        "items": [
            {
                "name":  it.product_name_snapshot,
                "variant": it.variant_label,
                "serial": it.serial_number,
                "qty":   it.qty,
                "unit_price": fmt(it.unit_price),
                "line_total": fmt(it.line_total),
            }
            for it in items
        ],
        "schedule": schedule,
        # Guarantors are filled in by Round 22 — `guarantor_rows` is still empty.
        # It is mapped through the mask now, rather than after the rows exist, so
        # that populating the source list inherits the masking instead of having
        # to remember it: a guarantor is the same PII class as the customer
        # (MUDD-55 H2 #1) and the default template already prints
        # `g.passport_id`.
        "guarantors": [
            mask_pii_fields(g, CONTRACT_GUARANTOR_PII_FIELDS if mask_pii else ())
            for g in guarantor_rows
        ],
        "today": long_date(_date.today(), locale),
        "totals": totals,
        "locale": locale,
        # Settings block bundles every visible business-profile field so a
        # custom template can pull a single object instead of 6 keys.
        "settings_block": {
            "business_name":  settings_row.business_name if settings_row else None,
            "receipt_footer": settings_row.receipt_footer if settings_row else None,
            "currency_label": currency_label(inst.currency, locale),
            # The CONTRACT's currency, not the shop's current setting.
            # This is the document a customer holds: re-reading the setting meant
            # reprinting a signed so'm contract as USD after the shop switched.
            "currency":       inst.currency,
        },
    }


async def _load_payment_context(
    db: AsyncSession, payment_id: UUID, *, locale: str = "uz",
) -> dict:
    from app.core.documents import (
        long_datetime,
        money,
        payment_method_label,
        payment_status_label,
    )

    pay = (await db.execute(select(Payment).where(Payment.id == payment_id))).scalar_one_or_none()
    if pay is None:
        raise ValueError(f"payment_not_found: {payment_id}")
    inst_ctx = await _load_installment_context(db, pay.installment_id, locale=locale)
    inst_ctx["payment"] = {
        "id":         str(pay.id),
        "short_id":   str(pay.id)[:8].upper(),
        # Read from the PAYMENT row, which is held equal to the contract's
        # currency by `fk_payments_contract_currency`. Never from the shop's
        # current setting: a receipt for a so'm contract must stay in so'm after
        # the shop switches to USD.
        "amount":     money(pay.amount or 0, pay.currency),
        "currency":   pay.currency,
        "currency_label": currency_label(pay.currency, locale),
        # Localized labels, not the enum's own value. `cash` and `approved`
        # printed in English inside an Uzbek receipt.
        "method":     payment_method_label(pay.method, locale),
        "status":     payment_status_label(pay.status, locale),
        # Converted to the business timezone and cut to the minute — the raw
        # column carries microseconds and an offset, which is both meaningless
        # to the reader and long enough to collide with its own label.
        "paid_at":    long_datetime(pay.paid_at, locale) if pay.paid_at else None,
        "external_ref": pay.external_ref,
        "notes":      pay.notes,
    }
    return inst_ctx


# ── render entry points ───────────────────────────────────────────────────

async def contract_html(
    db: AsyncSession, installment_id: UUID, *, locale: str = "uz",
    mask_pii: bool = False,
) -> str:
    """The contract's HTML, before it becomes bytes.

    Split out from the PDF call deliberately: the defects this document had —
    a footer over the clauses, a column running past the page — are LAYOUT
    facts, invisible in the template source and unreadable from a PDF byte
    string. Handing the same HTML to WeasyPrint's layout engine is what lets a
    test assert on where the text actually lands.

    Uses the tenant's custom HTML (TenantSettings.contract_template_html,
    Pro+ plan) if present, otherwise the bundled default. Custom HTML is
    validated at *save* time via `validate_custom_template` so render-time
    failures are limited to genuine WeasyPrint / data issues.
    """
    ctx = await _load_installment_context(db, installment_id, locale=locale, mask_pii=mask_pii)

    # Re-query the tenant's settings to read the (optional) custom template.
    # Kept separate from `ctx` so raw HTML never leaks into the template
    # variable space.
    inst = (await db.execute(
        select(Installment).where(Installment.id == installment_id)
    )).scalar_one()
    settings_row = (await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == inst.tenant_id)
    )).scalar_one_or_none()
    custom_html = (
        getattr(settings_row, "contract_template_html", None)
        if settings_row else None
    )

    if custom_html:
        tpl = _untrusted_env.from_string(custom_html)
    else:
        tpl = _env.get_template("contracts/default_uz.html")

    return tpl.render(**ctx)


async def render_contract_pdf(
    db: AsyncSession, installment_id: UUID, *, locale: str = "uz",
    mask_pii: bool = False,
) -> bytes:
    """The contract as PDF bytes. See :func:`contract_html` for the content.

    ``mask_pii`` defaults to False so every existing caller keeps its behaviour;
    the two routes that serve this document to a *person* pass the answer from
    ``app.core.pii_policy`` (MUDD-139).
    """
    from weasyprint import HTML

    html = await contract_html(db, installment_id, locale=locale, mask_pii=mask_pii)
    return await asyncio.to_thread(lambda: HTML(string=html, base_url=str(TEMPLATES_DIR), url_fetcher=_safe_pdf_url_fetcher).write_pdf())  # (#122) off event loop


async def render_contract_preview(
    db: AsyncSession, tenant_id: UUID, *, locale: str = "uz",
) -> bytes:
    """(#54) Render the contract template against SAMPLE data so an owner can
    preview the formatting before any real installment exists.

    Uses the tenant's real branding + the saved custom template (or the bundled
    default) with a placeholder customer/installment, so a fresh tenant gets a
    preview instead of a 404. The context mirrors ``_load_installment_context``
    exactly so the template's ``StrictUndefined`` never trips.
    """
    from datetime import date as _date

    from weasyprint import HTML

    from app.services.installments import add_months

    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
    settings_row = (await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
    )).scalar_one_or_none()

    today = _date.today()
    months = 6
    monthly = Decimal("500000")
    total = monthly * months
    sample_currency = currency_for_new_contract(settings_row)
    fmt = _fmt_for(sample_currency, locale)
    schedule = []
    for i in range(1, months + 1):
        schedule.append({
            "n": i,
            "due_date": long_date(add_months(today, i), locale),
            "amount": fmt(monthly),
        })

    ctx = {
        # The shop's profile, resolved in ONE place (services/tenant_profile).
        # This rendered `tenant.name` — the row the platform stamped at
        # provisioning — so a shop that renamed itself in Settings kept the old
        # name on every contract it generated. On a legal document.
        "tenant": _profile_ctx(tenant, settings_row),
        "customer": {
            "full_name": "Aliyev Vali Salimovich",
            "phone": "+998 90 123 45 67",
            "passport_id": "AA1234567",
            "passport_series": "AA",
            "passport_number": "1234567",
            "jshshir": "30112900010001",
            "birthdate": long_date(_date(1990, 1, 1), locale),
            "address": "Toshkent sh., Chilonzor tumani",
        },
        "installment": {
            "id": "00000000-0000-0000-0000-000000000000",
            "reference": "NAMUNA-0001",
            "contract_number": f"MDD-{today.year}-NAMUNA",
            "start_date": long_date(today, locale),
            "months": months,
            "interest_rate": None,
            "monthly_amount": fmt(monthly),
            "total_amount": fmt(total),
            "down_payment": fmt(0),
            "principal": fmt(total),
            "calc_formula": None,
            "late_fee_pct_per_day": None,
        },
        "items": [{
            "name": "Namuna mahsulot",
            "variant": None,
            "serial": None,
            "qty": 1,
            "unit_price": fmt(total),
            "line_total": fmt(total),
        }],
        "schedule": schedule,
        "guarantors": [],
        "today": long_date(today, locale),
        "locale": locale,
        "totals": {
            "principal": fmt(total),
            "down_payment": fmt(0),
            "total_amount": fmt(total),
            "monthly": fmt(monthly),
            "months": months,
            "remaining": fmt(total),
        },
        "settings_block": {
            "business_name":  settings_row.business_name if settings_row else None,
            "receipt_footer": settings_row.receipt_footer if settings_row else None,
            # A sample/quote has no contract behind it, so the shop's
            # CURRENT setting is the right answer here — it is what the next real
            # contract would be written in. The only surviving settings read.
            "currency":       currency_for_new_contract(settings_row),
            "currency_label": currency_label(currency_for_new_contract(settings_row), locale),
        },
    }

    custom_html = getattr(settings_row, "contract_template_html", None) if settings_row else None
    tpl = _untrusted_env.from_string(custom_html) if custom_html else _env.get_template("contracts/default_uz.html")
    return await asyncio.to_thread(lambda: HTML(string=tpl.render(**ctx), base_url=str(TEMPLATES_DIR), url_fetcher=_safe_pdf_url_fetcher).write_pdf())  # (#122) off event loop


async def receipt_html(db: AsyncSession, payment_id: UUID, *, locale: str = "uz") -> str:
    """The receipt's HTML. Split from the bytes for the same reason as the
    contract — the `Sana`/value collision only exists once laid out."""
    ctx = await _load_payment_context(db, payment_id, locale=locale)
    return _env.get_template("receipts/default.html").render(**ctx)


async def render_receipt_pdf(db: AsyncSession, payment_id: UUID, *, locale: str = "uz") -> bytes:
    from weasyprint import HTML

    html = await receipt_html(db, payment_id, locale=locale)
    return await asyncio.to_thread(lambda: HTML(string=html, base_url=str(TEMPLATES_DIR), url_fetcher=_safe_pdf_url_fetcher).write_pdf())  # (#122) off event loop


_INVOICE_STATUS_LABELS = {
    "open": "To'lanmagan",
    "paid": "To'langan",
    "overdue": "Muddati o'tgan",
    "void": "Bekor qilingan",
}


async def render_invoice_pdf(db: AsyncSession, invoice_id: UUID) -> bytes:
    """Render a subscription invoice PDF (Round 27)."""
    from weasyprint import HTML

    from app.models import Invoice, Tenant

    inv = (await db.execute(
        select(Invoice).where(Invoice.id == invoice_id)
    )).scalar_one_or_none()
    if inv is None:
        raise ValueError(f"invoice_not_found: {invoice_id}")
    tenant = (await db.execute(
        select(Tenant).where(Tenant.id == inv.tenant_id)
    )).scalar_one()

    line_items = inv.line_items or {}
    lines = line_items.get("lines", []) if isinstance(line_items, dict) else []

    ctx = {
        # NOT the shop's customer-facing profile, and deliberately not resolved
        # through `services/tenant_profile`. This is the PLATFORM's invoice TO
        # the shop, so it addresses the account as the platform knows it — the
        # name on the books and the billing address — not the trading name and
        # public number the shop shows its own customers. `billing_email` beside
        # it is the tell. Different document, different subject, different
        # source; see docs/SOURCE_OF_TRUTH.md.
        "tenant": {"name": tenant.name, "billing_email": tenant.billing_email},
        "invoice": {
            "id_short": str(inv.id)[:8].upper(),
            "status": inv.status.value,
            "status_label": _INVOICE_STATUS_LABELS.get(inv.status.value, inv.status.value),
            "amount": str(inv.amount),
            "issued_at": inv.issued_at.strftime("%Y-%m-%d") if inv.issued_at else "",
            "due_at": inv.due_at.strftime("%Y-%m-%d") if inv.due_at else "",
            "period_start": inv.period_start.isoformat(),
            "period_end": inv.period_end.isoformat(),
        },
        "lines": lines,
    }
    tpl = _env.get_template("invoices/default.html")
    html = tpl.render(**ctx)
    return await asyncio.to_thread(lambda: HTML(string=html, base_url=str(TEMPLATES_DIR), url_fetcher=_safe_pdf_url_fetcher).write_pdf())  # (#122) off event loop
