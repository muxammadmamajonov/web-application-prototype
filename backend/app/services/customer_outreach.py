"""Telegram messages sent to a customer who already uses the bot.

The operator's workflow used to end at "here is a link, send it to them
somehow". For a person the platform has never seen that is unavoidable. For one
who already has an account it is silly — we know their chat id, so the app can
just tell them.

Two moments matter:

  * a contract is created for them → "you have a new purchase: <product>"
  * they are added as a customer  → an invitation to join this shop

Both carry the same two buttons: open the app, and contact the shop. The second
exists because the first thing somebody thinks on receiving an unexpected
purchase notice is "I did not buy this" — and at that moment they need a way to
reach a human, not a support address they have to go and find.

Everything here is BEST-EFFORT. A failed send must never fail the request that
triggered it: the contract is real whether or not the notification arrived, and
`send_to_telegram` already swallows per-recipient Telegram errors and returns
False. The caller logs and moves on.
"""
from __future__ import annotations

import html
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Customer, Installment, InstallmentItem, Tenant, TenantSettings
from app.services import customer_identity, notifications

log = logging.getLogger(__name__)

#: uz/ru copy. The customer's own locale is not reliably known at this point
#: (they may never have opened the app), so the shop's default locale is used —
#: it is the best available guess about what language its customers read.
_COPY = {
    "uz": {
        "purchase_title": "Yangi xarid",
        "purchase_body": (
            "{shop} do'konida sizga yangi shartnoma rasmiylashtirildi:\n{items}\n\n"
            "Toʻlov jadvalini koʻrish uchun ilovani oching.\n"
            "Agar bu xaridni siz qilmagan boʻlsangiz, darhol biz bilan bogʻlaning."
        ),
        "invite_title": "Sizga taklif",
        "invite_body": (
            "{shop} sizni mijoz sifatida qoʻshdi.\n\n"
            "Shartnomalaringiz va toʻlov jadvalingizni koʻrish uchun ilovani oching."
        ),
        "open": "Ilovani ochish",
        "contact": "Bogʻlanish",
    },
    "ru": {
        "purchase_title": "Новая покупка",
        "purchase_body": (
            "В магазине {shop} на вас оформлен новый договор:\n{items}\n\n"
            "Откройте приложение, чтобы увидеть график платежей.\n"
            "Если эту покупку совершали не вы — сразу свяжитесь с нами."
        ),
        "invite_title": "Приглашение",
        "invite_body": (
            "{shop} добавил вас как клиента.\n\n"
            "Откройте приложение, чтобы увидеть свои договоры и график платежей."
        ),
        "open": "Открыть приложение",
        "contact": "Связаться",
    },
}


def _copy(locale: str | None) -> dict[str, str]:
    return _COPY.get((locale or "uz").lower()[:2], _COPY["uz"])


async def _shop(db: AsyncSession, tenant: Tenant) -> tuple[str, str | None, str | None]:
    """(display name, locale, contact telegram handle) for a tenant."""
    s = (await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
    )).scalar_one_or_none()
    name = (s.business_name if s and s.business_name else None) or tenant.name
    return name, (s.default_locale if s else None) or tenant.locale, (
        s.contact_telegram if s else None
    )


def _contact_url(handle: str | None) -> str | None:
    """A shop's Telegram handle as a button url.

    Only a Telegram handle works here: inline buttons accept http/https/tg:
    and nothing else, so `contact_phone` — the field an operator is most likely
    to have filled in — cannot become a button. When there is no handle the
    button is simply omitted rather than pointing somewhere useless.
    """
    # Order matters: an operator types "  @shop  ", so trim BEFORE stripping the
    # @, then re-check. Getting this wrong turns a blank field into a live
    # button pointing at https://t.me/ — a dead link is worse than no button.
    cleaned = str(handle or "").strip().lstrip("@").strip()
    return f"https://t.me/{cleaned}" if cleaned else None


async def _items_line(db: AsyncSession, installment: Installment) -> str:
    rows = (await db.execute(
        select(InstallmentItem.product_name_snapshot, InstallmentItem.qty)
        .where(InstallmentItem.installment_id == installment.id)
    )).all()
    named = [f"• {html.escape(str(n))}" + (f" ×{q}" if q and q > 1 else "")
             for n, q in rows if n]
    return "\n".join(named) if named else "• —"


async def open_app_url(db: AsyncSession, tenant: Tenant) -> str:
    """Where the "open the app" button should go.

    The shop's bot on t.me, so the Mini App opens INSIDE Telegram. A plain
    https link to the web app would kick the person out into a browser where
    they are not signed in — the worst possible landing for someone who just
    got an unexpected purchase notice. Falls back to the web app only when the
    tenant has no bot username at all.
    """
    from app.api.invitations import _tenant_bot_username
    from app.config import settings

    bot = await _tenant_bot_username(db, tenant)
    return f"https://t.me/{bot}" if bot else settings.frontend_url.rstrip("/")


async def notify_new_contract(
    db: AsyncSession, *, tenant: Tenant, customer: Customer, installment: Installment,
    app_url: str | None = None,
) -> bool:
    """Tell a reachable customer that a contract was opened in their name.

    Returns True when a message was actually sent. False covers both "there is
    nobody to tell" and "Telegram refused" — the caller treats them the same,
    because neither is a reason to fail the contract.
    """
    chat_id = await customer_identity.reachable_telegram_id(db, customer)
    if chat_id is None:
        return False

    name, locale, handle = await _shop(db, tenant)
    c = _copy(locale)
    body = c["purchase_body"].format(shop=html.escape(name), items=await _items_line(db, installment))
    buttons = [(c["open"], app_url or await open_app_url(db, tenant))]
    contact = _contact_url(handle)
    if contact:
        buttons.append((c["contact"], contact))

    ok = await notifications.send_to_telegram(
        chat_id, f"<b>{html.escape(c['purchase_title'])}</b>\n\n{body}", buttons=buttons,
    )
    log.info(
        "outreach.new_contract tenant=%s installment=%s sent=%s",
        tenant.id, installment.id, ok,
    )
    return ok


async def notify_customer_invited(
    db: AsyncSession, *, tenant: Tenant, customer: Customer, app_url: str,
) -> bool:
    """Deliver a shop's invitation straight to a customer who already has an
    account, instead of handing the operator a link to pass on by hand."""
    chat_id = await customer_identity.reachable_telegram_id(db, customer)
    if chat_id is None:
        return False

    name, locale, handle = await _shop(db, tenant)
    c = _copy(locale)
    buttons = [(c["open"], app_url)]
    contact = _contact_url(handle)
    if contact:
        buttons.append((c["contact"], contact))

    ok = await notifications.send_to_telegram(
        chat_id,
        f"<b>{html.escape(c['invite_title'])}</b>\n\n"
        + c["invite_body"].format(shop=html.escape(name)),
        buttons=buttons,
    )
    log.info("outreach.invited tenant=%s customer=%s sent=%s", tenant.id, customer.id, ok)
    return ok
