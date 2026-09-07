"""Tenant settings — single rich document per tenant.

Frontend renders this as 4-5 sub-pages but the API is a single GET + a
single PATCH (or per-section PATCH for finer control). PATCH only touches
the fields you pass; missing fields are left alone.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal, cast
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.tenant import _require_owner_or_manager, resolve_tenant
from app.config import settings as app_settings
from app.core.validators import LOGO_URL_MAX, UzPhoneOpt, validate_logo_url
from app.db import get_db
from app.models import Plan, Tenant, TenantSettings, TenantUser

log = logging.getLogger("muddato.tenant_settings")
router = APIRouter(prefix="/tenant", tags=["tenant.settings"])

CalcFormula = Literal["none", "annuity", "differentiated", "markup"]
ReminderTarget = Literal["owner", "channel", "both"]


# ── pydantic ────────────────────────────────────────────────────────────

class BusinessProfileIn(BaseModel):
    # (#810) Every string is bounded to its TenantSettings column width so an
    # over-length value is a clean 422 (not a Postgres truncation → 500), and
    # contact_phone/email are format-validated since they feed rendered
    # receipts/contracts.
    business_name:    str | None = Field(default=None, max_length=160)
    logo_url:         str | None = Field(default=None, max_length=LOGO_URL_MAX)

    @field_validator("business_name")
    @classmethod
    def _check_business_name(cls, v: str | None) -> str | None:
        # Omitting the field leaves the name alone; explicitly sending "" (or
        # spaces) blanked it. The name is not decoration — it is rendered onto
        # the contract and receipt PDFs (services/pdf.py), so a blank one puts
        # an unnamed shop on a legal document. Found by the QA re-test, once the
        # payload was nested correctly.
        if v is None:
            return None
        name = v.strip()
        if not name:
            raise ValueError("business_name_blank")
        return name

    @field_validator("logo_url")
    @classmethod
    def _check_logo_url(cls, v: str | None) -> str | None:
        # (#14) Rejected here as a 422 with a named reason, not stored and left
        # to render as a broken image nobody notices. The frontend refuses the
        # same values inline, so this is the second line, not the only one.
        return validate_logo_url(v)
    address:          str | None = Field(default=None, max_length=500)
    contact_phone:    UzPhoneOpt = Field(default=None, max_length=32)
    contact_email:    EmailStr | None = Field(default=None, max_length=160)
    contact_telegram: str | None = Field(default=None, max_length=64)
    business_hours:   dict | None = None
    currency:         str | None = Field(default=None, max_length=8)
    default_locale:   str | None = Field(default=None, max_length=8)
    receipt_footer:   str | None = Field(default=None, max_length=500)
    # Segment — lives on Tenant (not TenantSettings); handled specially below.
    business_type:       str | None = Field(default=None, max_length=48)
    business_type_other: str | None = Field(default=None, max_length=120)


class RemindersIn(BaseModel):
    reminder_target:             ReminderTarget | None = None
    reminder_channel_id:         str | None = None
    reminder_overdue_every_days: int | None = Field(default=None, ge=1, le=30)
    reminder_offsets_days:       list[int] | None = None
    reminder_templates:          dict | None = None
    notification_prefs:          dict | None = None


class CalcDefaultsIn(BaseModel):
    default_calc_formula:         CalcFormula | None = None
    default_markup_pct:           Decimal | None = Field(default=None, ge=0, le=200)
    default_max_months:           int | None = Field(default=None, ge=1, le=120)
    default_late_fee_pct_per_day: Decimal | None = Field(default=None, ge=0, le=10)
    default_min_down_pct:         Decimal | None = Field(default=None, ge=0, le=100)
    early_payoff_discount_pct:    Decimal | None = Field(default=None, ge=0, le=50)
    commission_rate_pct:          Decimal | None = Field(default=None, ge=0, le=100)


class PaymentsIn(BaseModel):
    payment_methods:           dict | None = None
    click_merchant_id:         str | None = None
    click_service_id:          str | None = None
    click_secret_key:          str | None = None
    payme_merchant_id:         str | None = None
    payme_secret_key:          str | None = None
    card_payment_instructions: str | None = None


class RiskNetworkIn(BaseModel):
    risk_network_opt_in: bool | None = None


class SmsConfigIn(BaseModel):
    sms_budget_uzs_per_month: int | None = Field(default=None, ge=0, le=100_000_000)
    sms_sender_alpha:         str | None = Field(default=None, max_length=16)


class TenantSettingsPatch(BaseModel):
    """Union patch — pass any combination of the sub-sections."""
    profile:       BusinessProfileIn | None = None
    reminders:     RemindersIn | None = None
    calc:          CalcDefaultsIn | None = None
    payments:      PaymentsIn | None = None
    customer_form: CustomerFormIn | None = None
    product_form:  ProductFormIn | None = None
    risk:          RiskNetworkIn | None = None
    sms:           SmsConfigIn | None = None


# ── helpers ─────────────────────────────────────────────────────────────

async def _get_or_create_settings(db: AsyncSession, tenant: Tenant) -> TenantSettings:
    # Local import: `app.services.scheduler` pulls in the whole job graph, and a
    # module-level import here would make an API module depend on it at startup.
    from app.services.scheduler import default_reminder_templates

    s = (await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if s is None:
        # (#14) Seeded from the tenant's name for the same reason as the
        # provisioning path: this is the lazy-creation route an admin-created
        # business takes, and it is exactly where the blank profile came from.
        s = TenantSettings(tenant_id=tenant.id, business_name=tenant.name)
        # (#MUDD-111) Seeded from the ONE canonical set in the scheduler, which
        # is also what the send path falls back to and what the admin defaults
        # endpoint publishes.
        #
        # This used to be a second, hand-written dict — and it was the defect the
        # owner reported. It hardcoded "UZS" (so a shop trading in USD dunned its
        # customers in the wrong unit) and it used placeholder names the cron
        # never supplied, so every tenant created through this lazy path shipped
        # a literal "{days_left}" and "{contract}" to real customers.
        s.reminder_templates = default_reminder_templates()
        db.add(s)
        await db.commit()
        await db.refresh(s)
    return s


class CustomerFormIn(BaseModel):
    """Custom field definitions controlled by the tenant."""
    fields:       list[dict] | None = None
    auto_approve: bool | None = None


class ProductFormIn(BaseModel):
    """Named, typed catalog attribute definitions controlled by the tenant."""
    fields: list[dict] | None = None


# Sensible out-of-the-box catalog fields for a phone shop. Returned whenever
# the tenant hasn't customised their own — so the product form is never blank.
DEFAULT_PRODUCT_FIELDS: list[dict] = [
    {"key": "brand",   "label": "Brend",      "type": "text",   "required": True,  "order": 0},
    {"key": "model",   "label": "Model",      "type": "text",   "required": True,  "order": 1},
    {"key": "storage", "label": "Xotira",     "type": "text",   "required": False, "order": 2},
    {"key": "color",   "label": "Rang",       "type": "text",   "required": False, "order": 3},
    {"key": "imei1",   "label": "IMEI 1",     "type": "imei",   "required": True,  "max_length": 14, "order": 4},
    {"key": "imei2",   "label": "IMEI 2",     "type": "imei",   "required": False, "max_length": 14, "order": 5},
]


class BotConfigIn(BaseModel):
    bot_mode: Literal["shared", "tenant"] | None = None
    # Raw bot token; we'll validate via getMe, then store ciphertext.
    # (LOW-4) Bounded: this value is interpolated into the Telegram API URL
    # (`.../bot{token}/getMe`) and then encrypted into `EncryptedStr(500)`, so an
    # unbounded string meant an unbounded outbound URL and a ciphertext that
    # could overflow the column at write time. A real token is `<id>:<35 chars>`
    # — well under 128; the cap only refuses input no token can be.
    bot_token: str | None = Field(default=None, max_length=128)


def _resolved_name(s: TenantSettings, tenant: Tenant | None) -> str | None:
    """(#14) The Business Profile must never render blank while the admin list
    shows a name: rows created before the seeding below carry no
    `business_name`, and a read-side fallback fixes those without a migration
    that would have to guess whether a blank was deliberate. A stored value
    always wins, so the owner can still rename their business.

    Routed through the shared resolver so this form shows exactly the name every
    other surface renders. The precedence itself is stated once, in
    `services/tenant_profile`.
    """
    if tenant is None:
        return s.business_name
    from app.services.tenant_profile import resolve

    return resolve(tenant, s).name


def _serialize(s: TenantSettings, tenant: Tenant | None = None, *, plan_allows_bot: bool = False) -> dict:
    return {
        "profile": {
            "business_name":    _resolved_name(s, tenant),
            "logo_url":         s.logo_url,
            "address":          s.address,
            "contact_phone":    s.contact_phone,
            "contact_email":    s.contact_email,
            "contact_telegram": s.contact_telegram,
            "business_hours":   s.business_hours or {},
            "currency":         s.currency,
            "default_locale":   s.default_locale,
            "receipt_footer":   s.receipt_footer,
            "business_type":       tenant.business_type if tenant else None,
            "business_type_other": tenant.business_type_other if tenant else None,
        },
        "reminders": {
            "reminder_target":             s.reminder_target,
            "reminder_channel_id":         s.reminder_channel_id,
            "reminder_overdue_every_days": s.reminder_overdue_every_days,
            "reminder_offsets_days":       s.reminder_offsets_days or [],
            "reminder_templates":          s.reminder_templates or {},
            "notification_prefs":          s.notification_prefs or {},
        },
        "calc": {
            "default_calc_formula":         s.default_calc_formula,
            "default_markup_pct":           float(s.default_markup_pct or 0),
            "default_max_months":           s.default_max_months,
            "default_late_fee_pct_per_day": float(s.default_late_fee_pct_per_day or 0),
            "default_min_down_pct":         float(s.default_min_down_pct or 0),
            "early_payoff_discount_pct":    float(getattr(s, "early_payoff_discount_pct", 0) or 0),
            "commission_rate_pct":          float(getattr(s, "commission_rate_pct", 0) or 0),
        },
        "payments": {
            "payment_methods":           s.payment_methods or {},
            "click_merchant_id":         s.click_merchant_id,
            "click_service_id":          s.click_service_id,
            "click_secret_key":          _mask(s.click_secret_key),
            "payme_merchant_id":         s.payme_merchant_id,
            "payme_secret_key":          _mask(s.payme_secret_key),
            "card_payment_instructions": s.card_payment_instructions,
        },
        "bot": {
            "bot_mode":         s.bot_mode,
            "bot_username":     s.bot_username,
            "bot_activated_at": s.bot_activated_at.isoformat() if s.bot_activated_at else None,
            # Don't echo the token back, only show "is set" hint
            "bot_token_set":    bool(s.bot_token_ciphertext),
            # (#39) Whether the tenant's PLAN permits a dedicated bot, so the UI can
            # mark the feature plan-locked UPFRONT instead of 403-ing after the
            # owner walks the wizard and pastes a token.
            "plan_allows_bot":  plan_allows_bot,
        },
        "customer_form": {
            "fields":       s.customer_form_fields or [],
            "auto_approve": s.auto_approve_customers,
        },
        "product_form": {
            "fields": s.product_form_fields or DEFAULT_PRODUCT_FIELDS,
        },
        "risk": {
            "risk_network_opt_in": bool(getattr(s, "risk_network_opt_in", False)),
        },
        "sms": {
            "sms_budget_uzs_per_month": int(getattr(s, "sms_budget_uzs_per_month", 0) or 0),
            "sms_sender_alpha":         getattr(s, "sms_sender_alpha", None),
        },
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


def _mask(secret: str | None) -> str | None:
    """Avoid leaking secrets back to the client — show only last 4 chars."""
    if not secret:
        return None
    return "*" * max(0, len(secret) - 4) + secret[-4:]


# ── endpoints ───────────────────────────────────────────────────────────

async def _plan_allows_bot(db: AsyncSession, tenant: Tenant) -> bool:
    """(#39) True if the tenant's plan permits a dedicated Telegram bot (bot_mode='tenant')."""
    plan = (await db.execute(select(Plan).where(Plan.code == tenant.plan_code))).scalar_one_or_none()
    return bool(plan and plan.bot_mode == "tenant")


@router.get("/settings")
async def get_settings(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Owner/manager only, matching PATCH below.

    Provider secrets are masked, but the payload still carries the shop's pricing
    policy (`calc`) and the bank card customers transfer to
    (`card_payment_instructions`) — read access has to match write access.
    """
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    s = await _get_or_create_settings(db, tenant)
    return _serialize(s, tenant, plan_allows_bot=await _plan_allows_bot(db, tenant))


@router.patch("/settings")
async def patch_settings(
    body: TenantSettingsPatch,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    # (QA L-3) This is a NESTED patch and Pydantic drops what it does not
    # recognise, so a caller sending the flat `{"logo_url": …}` instead of
    # `{"profile": {"logo_url": …}}` got a cheerful 200 with nothing saved —
    # the settings screen still showing the old value, no hint the write had
    # been discarded.

    # Checked HERE rather than via `extra="forbid"` on the model, for two
    # reasons that testing surfaced. Model validation runs BEFORE the handler,
    # so it pre-empted the role guard above and answered an unauthorised
    # `sales` with a schema 422 instead of 403. And forbidding unknown keys
    # makes the endpoint brittle to frontend/backend version skew — they deploy
    # separately, so a cached older bundle sending one stale key would break
    # every settings save. Rejecting a patch that names no section at all
    # catches the real mistake and neither of those.
    if not body.model_dump(exclude_unset=True, exclude_none=True):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            'empty_patch — expected at least one section, e.g. {"profile": {…}}',
        )
    s = await _get_or_create_settings(db, tenant)

    # business_type / business_type_other live on Tenant, not TenantSettings.
    if body.profile is not None:
        pdata = body.profile.model_dump(exclude_unset=True)
        if "business_type" in pdata:
            tenant.business_type = pdata.pop("business_type")
        if "business_type_other" in pdata:
            tenant.business_type_other = pdata.pop("business_type_other")

    # (MUDD-137) Money-affecting fields only — pricing policy (calc) and payment
    # provider config (payments). Everything else this endpoint touches
    # (profile, reminders, custom-field forms, risk opt-in, SMS budget) is
    # cosmetic/operational, not something that changes what a customer owes or
    # where money settles, so it stays out of the audit log to keep it small
    # and forensically useful — the same principle AUDITED_ACTIONS documents.
    # Secret values (click/payme secret_key) are never written to the log,
    # only whether the stored value changed.
    _AUDITED_CALC_FIELDS = (
        "default_calc_formula", "default_markup_pct", "default_max_months",
        "default_late_fee_pct_per_day", "default_min_down_pct",
        "early_payoff_discount_pct", "commission_rate_pct",
    )
    _AUDITED_PAYMENT_FIELDS = (
        "payment_methods", "click_merchant_id", "click_service_id",
        "click_secret_key", "payme_merchant_id", "payme_secret_key",
    )

    def _snapshot(fields: tuple[str, ...]) -> dict[str, object]:
        out: dict[str, object] = {}
        for k in fields:
            v = getattr(s, k, None)
            if k.endswith("secret_key"):
                out[k] = bool(v)          # presence only, never the value
            elif isinstance(v, Decimal):
                out[k] = float(v)
            else:
                out[k] = v
        return out

    def _submitted_secret_rotations(group_obj: BaseModel | None) -> set[str]:
        """Secret fields submitted with a real (unmasked, non-empty) value.

        `encrypt_secret` is Fernet — non-deterministic — so ciphertext can
        never be compared for equality, and `_snapshot`'s presence bool stays
        True on both sides of a "secret A -> secret B" rotation. The
        submission itself is the only signal available; no plaintext is
        touched here.
        """
        if group_obj is None:
            return set()
        return {
            k for k, v in group_obj.model_dump(exclude_unset=True).items()
            if k.endswith("secret_key") and isinstance(v, str) and v and not v.startswith("*")
        }

    before_calc = _snapshot(_AUDITED_CALC_FIELDS) if body.calc is not None else {}
    before_payments = _snapshot(_AUDITED_PAYMENT_FIELDS) if body.payments is not None else {}
    # (MUDD-137 review) card_payment_instructions tells the CUSTOMER which card to
    # transfer money to — the highest-value field in this tuple — but it routinely
    # holds a card number and audit_log.tenant_id is ON DELETE SET NULL, so it must
    # never be written verbatim. Only whether it changed is recorded, same shape as
    # the secret-rotation marker below.
    before_card_instructions = s.card_payment_instructions

    def _apply(group_obj: BaseModel | None) -> None:
        if group_obj is None:
            return
        for k, v in group_obj.model_dump(exclude_unset=True).items():
            # Don't overwrite stored secrets with their masked echo.
            if k.endswith("secret_key") and isinstance(v, str) and v.startswith("*"):
                continue
            # (#84) Index the Payme secret by a blind HMAC so the webhook does an
            # O(1) lookup instead of decrypting every tenant's key — computed from
            # the plaintext, before the encrypt step just below.
            if k == "payme_secret_key" and isinstance(v, str):
                from app.core.crypto import blind_index
                s.payme_secret_bidx = blind_index(v, context="payme_secret") if v else None
            # Encrypt provider secrets at rest before storing.
            if k.endswith("secret_key") and isinstance(v, str) and v:
                from app.core.crypto import encrypt_secret
                v = encrypt_secret(v)
            # Handled on the Tenant row above — not a TenantSettings column.
            if k in ("business_type", "business_type_other"):
                continue
            setattr(s, k, v)

    _apply(body.profile)
    _apply(body.reminders)
    _apply(body.calc)
    _apply(body.payments)
    _apply(body.risk)
    _apply(body.sms)

    if body.calc is not None or body.payments is not None:
        after_calc = _snapshot(_AUDITED_CALC_FIELDS) if body.calc is not None else {}
        after_payments = _snapshot(_AUDITED_PAYMENT_FIELDS) if body.payments is not None else {}
        before_all = {**before_calc, **before_payments}
        after_all = {**after_calc, **after_payments}
        changed_before = {k: v for k, v in before_all.items() if v != after_all.get(k)}
        changed_after = {k: v for k, v in after_all.items() if k in changed_before}
        # A rotation (secret A -> secret B) never appears above — presence
        # doesn't change — so it needs its own marker or it is silently
        # unaudited on the single most security-relevant transition.
        for field in _submitted_secret_rotations(body.payments):
            changed_before[f"{field}_rotated"] = False
            changed_after[f"{field}_rotated"] = True
        if (
            body.payments is not None
            and "card_payment_instructions" in body.payments.model_dump(exclude_unset=True)
            and s.card_payment_instructions != before_card_instructions
        ):
            changed_before["card_payment_instructions_changed"] = False
            changed_after["card_payment_instructions_changed"] = True
        if changed_after:
            from app.services import audit
            await audit.record(
                db, action="tenant_settings.update", tenant_id=tenant.id,
                actor_user_id=membership.user_id, entity_type="tenant_settings",
                entity_id=str(s.id), before=changed_before, after=changed_after,
            )
    if body.customer_form:
        if body.customer_form.fields is not None:
            # Cap the number of custom fields by the tenant's plan. Trial
            # & Lite get 0 (only the built-in fields), Pro 20, Custom ~999.
            plan = (await db.execute(
                select(Plan).where(Plan.code == tenant.plan_code)
            )).scalar_one_or_none()
            cap = cast(int, plan.max_custom_fields) if plan else 0
            if len(body.customer_form.fields) > cap:
                raise HTTPException(
                    status.HTTP_402_PAYMENT_REQUIRED,
                    f"plan_custom_fields_limit:{cap}",
                )
            s.customer_form_fields = body.customer_form.fields
        if body.customer_form.auto_approve is not None:
            s.auto_approve_customers = body.customer_form.auto_approve

    if body.product_form and body.product_form.fields is not None:
        # Same plan cap as customer fields — Trial/Lite get the built-in
        # defaults only (cap 0), Pro 20, Custom ~999.
        plan = (await db.execute(
            select(Plan).where(Plan.code == tenant.plan_code)
        )).scalar_one_or_none()
        cap = cast(int, plan.max_custom_fields) if plan else 0
        if len(body.product_form.fields) > cap:
            raise HTTPException(
                status.HTTP_402_PAYMENT_REQUIRED,
                f"plan_custom_fields_limit:{cap}",
            )
        s.product_form_fields = body.product_form.fields

    await db.commit()
    await db.refresh(s)
    # NO `db.refresh(tenant)` here. Under RLS the commit above ends the
    # transaction, and the next statement runs in a fresh one as the restricted
    # `muddato_app` role — which cannot SELECT the `tenants` row the way
    # `resolve_tenant` could (it loaded it as `postgres`, before the scope was
    # pinned). The refresh then finds no row and SQLAlchemy raises
    # "Could not refresh instance '<Tenant>'", surfacing to the user as
    # "Temporary server error". It is also unnecessary: the session is
    # `expire_on_commit=False`, so `tenant` still holds every value this
    # handler set. Observed in production 2026-08-06.
    return _serialize(s, tenant, plan_allows_bot=await _plan_allows_bot(db, tenant))


# ── tenant bot activation (Pro+ plans only) ────────────────────────────

@router.post("/settings/bot/activate")
async def activate_tenant_bot(
    body: BotConfigIn,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Validate the tenant's bot token via Telegram getMe, set its webhook
    to point at our /api/bot/t/<tenant_id>/webhook endpoint, save the
    bot_username + ciphertext for later use."""
    tenant, membership = ctx
    _require_owner_or_manager(membership)

    if not body.bot_token:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bot_token_required")

    plan = (await db.execute(select(Plan).where(Plan.code == tenant.plan_code))).scalar_one_or_none()
    if not plan or plan.bot_mode != "tenant":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "plan_does_not_allow_tenant_bot")

    # 1) Validate the token: getMe must succeed and return a bot.
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.get(f"https://api.telegram.org/bot{body.bot_token}/getMe")
    except httpx.HTTPError as e:
        # (#147) Can't reach Telegram → 502, not an unhandled 500.
        log.warning("tenant_bot_getMe_unreachable tenant=%s err=%s", tenant.id, type(e).__name__)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "telegram_unreachable") from e
    if r.status_code != 200:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"bot_invalid: {r.text[:200]}")
    data = r.json()
    if not data.get("ok") or not data.get("result", {}).get("is_bot"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "not_a_bot")
    bot_username = data["result"]["username"]

    # 2) Set webhook → backend's /api/bot/t/<tenant_id>/webhook. The
    #    secret_token is derived deterministically from SECRET_KEY +
    #    tenant_id so we can verify it on every incoming update without
    #    storing yet another secret per tenant. See app.api.bot._tenant_secret.
    from app.api.bot import _tenant_secret  # local import to dodge a circle
    webhook_url = f"{app_settings.backend_url.rstrip('/')}/api/bot/t/{tenant.id}/webhook"
    secret_token = _tenant_secret(tenant.id)
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.post(
                f"https://api.telegram.org/bot{body.bot_token}/setWebhook",
                data={
                    "url": webhook_url,
                    "secret_token": secret_token,
                    "drop_pending_updates": "true",
                    "allowed_updates": '["message","callback_query","my_chat_member"]',
                },
            )
        if r.status_code != 200 or not r.json().get("ok"):
            log.warning("tenant_bot_setWebhook_failed tenant=%s body=%s", tenant.id, r.text[:300])
            # Don't fail the whole activation — they can retry; the token is still saved.
    except httpx.HTTPError as e:
        # (#147) Network error reaching Telegram — non-fatal too (token saved; retry later).
        log.warning("tenant_bot_setWebhook_unreachable tenant=%s err=%s", tenant.id, type(e).__name__)

    # 3a) Register slash commands + chat menu so the new bot looks
    #     like @MuddatoBot from day one. Errors here are non-fatal
    #     (token validity is already proven by the getMe call above).
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            await http.post(
                f"https://api.telegram.org/bot{body.bot_token}/setMyCommands",
                json={"commands": [
                    {"command": "start",  "description": "Boshlash"},
                    {"command": "login",  "description": "Hisobga kirish"},
                    {"command": "help",   "description": "Yordam"},
                ]},
            )
            await http.post(
                f"https://api.telegram.org/bot{body.bot_token}/setChatMenuButton",
                json={"menu_button": {
                    "type": "web_app",
                    "text": "Muddato'ni ochish",
                    "web_app": {"url": app_settings.frontend_url.rstrip("/")},
                }},
            )
    except Exception as e:
        # (#742) Log the exception TYPE only — these calls embed the bot token in
        # the URL, and an HTTPStatusError's message includes that URL.
        log.warning("tenant_bot_setup_commands_failed tenant=%s err=%s", tenant.id, type(e).__name__)

    # 3) Persist
    s = await _get_or_create_settings(db, tenant)
    s.bot_mode = "tenant"
    s.bot_token_ciphertext = body.bot_token   # encrypted at rest by EncryptedStr (#83)
    s.bot_username = bot_username
    s.bot_activated_at = datetime.now(UTC)

    from app.services import audit
    await audit.record(
        db, action="tenant.bot_activate", tenant_id=tenant.id,
        actor_user_id=membership.user_id, entity_type="tenant_settings",
        entity_id=str(tenant.id), after={"bot_username": bot_username},
    )
    await db.commit()
    await db.refresh(s)

    return {"ok": True, "bot_username": bot_username, "webhook_url": webhook_url}


@router.post("/settings/bot/deactivate")
async def deactivate_tenant_bot(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Revert to shared @MuddatoBot. We don't delete the bot upstream
    (tenant retains ownership) — just stop using it."""
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    s = await _get_or_create_settings(db, tenant)
    # Best-effort: unset the webhook on the tenant's bot so it doesn't
    # keep sending us updates we won't process.
    if s.bot_token_ciphertext:
        try:
            async with httpx.AsyncClient(timeout=5.0) as http:
                await http.post(f"https://api.telegram.org/bot{s.bot_token_ciphertext}/deleteWebhook")
        except Exception as e:
            # (#742) Exception TYPE only — the URL (with the bot token) must not
            # reach the logs via httpx.HTTPStatusError.__str__.
            log.warning("tenant_bot_unset_webhook_failed tenant=%s err=%s", tenant.id, type(e).__name__)
    s.bot_mode = "shared"
    s.bot_token_ciphertext = None
    s.bot_username = None
    s.bot_activated_at = None
    await db.commit()

    # Drop the cached aiogram Bot so the next activate (possibly with a
    # different token) doesn't accidentally reuse this session.
    from app.api.bot import _invalidate_tenant_bot
    _invalidate_tenant_bot(tenant.id)

    return {"ok": True}


# ── tenant staff (a.k.a. team / invitations) ──────────────────────────

class StaffRow(BaseModel):
    id:                 str
    telegram_id:        int
    telegram_username:  str | None = None
    first_name:         str | None = None
    last_name:          str | None = None
    role:               str
    is_active:          bool
    last_seen_at:       str | None = None
    invitation_status:  str    # "active" | "pending" | "rejected"


class StaffPatch(BaseModel):
    role:      Literal["owner", "manager", "sales"] | None = None
    is_active: bool | None = None


@router.get("/staff", response_model=list[StaffRow])
async def list_staff(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[StaffRow]:
    """Return all tenant_users for this tenant, joined with the User row.

    Owner/manager only, like the update/revoke siblings below: the roster names
    every colleague's Telegram identity and marks who the owner is.
    """
    from app.models import User
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    rows = (await db.execute(
        select(TenantUser, User)
        .join(User, User.id == TenantUser.user_id)
        .where(TenantUser.tenant_id == tenant.id)
        .order_by(TenantUser.created_at.desc())
    )).all()
    return [
        StaffRow(
            id=str(tu.id),
            telegram_id=u.telegram_id,
            telegram_username=u.telegram_username,
            first_name=u.first_name,
            last_name=u.last_name,
            role=tu.role.value,
            is_active=tu.is_active,
            last_seen_at=u.last_seen_at.isoformat() if u.last_seen_at else None,
            invitation_status=tu.invitation_status,
        )
        for (tu, u) in rows
    ]


async def _active_owner_count(db: AsyncSession, tenant_id) -> int:
    """How many active owners the tenant has left.

    Three separate paths can strand a tenant with nobody able to administer it —
    demoting an owner, deactivating one, and deleting one — so the count lives
    in one place rather than being re-derived at each call site. It was the
    absence of this check on the DEMOTE path, while the other two had it, that
    let a sole owner lock their own business out (QA C-1).
    """
    from sqlalchemy import func

    from app.models.enums import TenantRole
    return (await db.execute(
        select(func.count(TenantUser.id)).where(
            TenantUser.tenant_id == tenant_id,
            TenantUser.role == TenantRole.owner,
            TenantUser.is_active.is_(True),
        )
    )).scalar_one()


@router.patch("/staff/{tu_id}")
async def update_staff(
    tu_id: UUID,
    body: StaffPatch,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    from app.models.enums import TenantRole
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    tu = (await db.execute(
        select(TenantUser).where(TenantUser.id == tu_id, TenantUser.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if tu is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "membership_not_found")
    before_role, before_active = tu.role.value, tu.is_active
    # Only an owner can change roles or revoke owners.
    if body.role is not None:
        if membership.role != TenantRole.owner:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_for_role_change")
        new_role = TenantRole(body.role)
        # (QA C-1) Demoting the LAST owner leaves the tenant with nobody who can
        # administer it — and no way back, since changing a role is itself
        # owner-gated. `revoke_staff` and the is_active branch below already
        # refuse this; the role path did not, so one unconfirmed click in the
        # staff screen could strand a business until a platform admin
        # intervened. Re-asserting the same role is not a demotion and is
        # allowed, as is stepping down once a successor has been promoted.
        if (tu.role == TenantRole.owner and new_role != TenantRole.owner
                and tu.is_active and await _active_owner_count(db, tenant.id) <= 1):
            raise HTTPException(status.HTTP_409_CONFLICT, "cannot_remove_last_owner")
        tu.role = new_role
    if body.is_active is not None:
        # (audit/sec) The role gate above only guards role changes. is_active needs
        # its own owner guard, or a manager could deactivate (lock out) the owner.
        if tu.role == TenantRole.owner:
            if membership.role != TenantRole.owner:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_for_owner_change")
            # Never deactivate the last active owner (mirrors revoke_staff).
            # `and` short-circuits, so the count query still only runs when this
            # request is actually a deactivation.
            if body.is_active is False and await _active_owner_count(db, tenant.id) <= 1:
                raise HTTPException(status.HTTP_409_CONFLICT, "cannot_deactivate_last_owner")
        tu.is_active = body.is_active
    if tu.role.value != before_role or tu.is_active != before_active:
        from app.services import audit
        await audit.record(
            db, action="staff.update", tenant_id=tenant.id,
            actor_user_id=membership.user_id, entity_type="tenant_user",
            entity_id=str(tu.id),
            before={"role": before_role, "is_active": before_active},
            after={"role": tu.role.value, "is_active": tu.is_active},
        )
    await db.commit()
    return {"id": str(tu.id), "role": tu.role.value, "is_active": tu.is_active}


@router.delete("/staff/{tu_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_staff(
    tu_id: UUID,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    from app.models.enums import TenantRole
    tenant, membership = ctx
    if membership.role != TenantRole.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only")
    tu = (await db.execute(
        select(TenantUser).where(TenantUser.id == tu_id, TenantUser.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if tu is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "membership_not_found")
    # Refuse to delete the last owner — at least one owner must remain. `and`
    # short-circuits, so the count query still only runs for an owner.
    if tu.role == TenantRole.owner and await _active_owner_count(db, tenant.id) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "cannot_remove_last_owner")
    from app.services import audit
    await audit.record(
        db, action="staff.revoke", tenant_id=tenant.id,
        actor_user_id=membership.user_id, entity_type="tenant_user",
        entity_id=str(tu.id),
        before={"role": tu.role.value, "user_id": str(tu.user_id)},
    )
    await db.delete(tu)
    await db.commit()


# ── Round 18 endpoints: reminder test send + holidays list ─────────────

class TestReminderIn(BaseModel):
    template_key: Literal["before_due", "due_today", "overdue", "owner_overdue_alert"]
    # Optional override of the stored template — lets the user preview an
    # in-flight edit without saving first.
    template_override: str | None = None


@router.post("/settings/reminders/test")
async def test_reminder_send(
    body: TestReminderIn,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Send the chosen reminder template to the *current user's* Telegram with
    fake but realistic placeholder values. Lets owners preview a template edit
    before it goes live to real customers.
    """
    tenant, membership = ctx
    _require_owner_or_manager(membership)

    from app.core.currency import currency_for_new_contract
    from app.models import User
    from app.services.notifications import notify_user, reminder_context, render
    from app.services.scheduler import default_reminder_templates

    user = (await db.execute(select(User).where(User.id == membership.user_id))).scalar_one()
    if not user.telegram_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "user_has_no_telegram_id")

    s = await _get_or_create_settings(db, tenant)
    templates = (s.reminder_templates or {})
    tpl = (
        body.template_override
        or templates.get(body.template_key)
        or default_reminder_templates().get(body.template_key, "")
    )
    if not tpl:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template_unset")

    # (#MUDD-111) The SAME builder the 09:00 cron uses. This block used to be a
    # rival dict that happened to include `days_left` / `contract` / `phone`,
    # so the owner's "test send" rendered flawlessly while the real send left
    # those placeholders literal — the preview was hiding the bug it existed to
    # catch. Sharing the builder makes that divergence unrepresentable.
    ctx_vars = reminder_context(
        customer_name=user.first_name or "Test mijoz",
        # A preview has no contract, so it shows what the shop's NEXT
        # contract would be written in — the one currency read that is still a
        # settings read, and correct because nothing is being relabelled.
        currency=currency_for_new_contract(s),
        amount=850_000,
        due_date="2026-06-03",
        contract_number="MDD-2026-000123",
        remaining=2_550_000,
        days_overdue=2,
        days_until_due=3,
        phone="+998901234567",
    )
    text = render(tpl, **ctx_vars)
    ok = await notify_user(
        db,
        user_id=user.id,
        tenant_id=tenant.id,
        kind="reminder.test",
        title=f"📋 Test: {body.template_key}",
        body=text,
        persist=False,
    )
    return {"sent": ok, "rendered": text}


@router.get("/settings/holidays")
async def list_tenant_holidays(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    year: int | None = None,
) -> list[dict]:
    """Read-only holiday list — used by the Settings → Reminders UI so the
    owner sees which days the cron will skip."""
    tenant, _ = ctx
    _ = tenant  # not used (holidays are platform-wide), kept for guard parity
    from app.models import Holiday

    q = select(Holiday).order_by(Holiday.holiday_date.asc())
    if year is not None:
        from datetime import date as _date
        q = q.where(
            Holiday.holiday_date >= _date(year, 1, 1),
            Holiday.holiday_date < _date(year + 1, 1, 1),
        )
    rows = (await db.execute(q)).scalars().all()
    return [
        {
            "date": h.holiday_date.isoformat(),
            "name_uz": h.name_uz,
            "name_ru": h.name_ru,
            "kind": h.kind,
        }
        for h in rows
    ]

