"""Telegram bot — primarily a launcher for the MuddatoPages WebApp."""
from __future__ import annotations

import html
import logging
from datetime import UTC, datetime
from typing import Final

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from sqlalchemy import select

from app.config import settings
from app.core import invite_codes

# (Batch 5) One bot process serves every tenant's chats, so its DB work runs
# under the explicit WORKER authority.
from app.db import worker_session
from app.models import User
from app.services.referral import record_referral_intent

logger = logging.getLogger("muddato.bot")

WEBAPP_URL: Final[str] = settings.frontend_url.rstrip("/")

router = Router(name="muddato-bot")


def _open_webapp_keyboard(label_open: str = "Muddato'ni ochish") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label_open, web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Muddato'ni oching →",
    )


def _login_inline(label: str = "Hisobga kirish") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, web_app=WebAppInfo(url=f"{WEBAPP_URL}/auth/login")),
    ]])


async def _ensure_user(message: Message) -> User | None:
    """Idempotently upsert a User row for the message's sender."""
    if message.from_user is None:
        return None
    async with worker_session() as db:
        user = (await db.execute(select(User).where(User.telegram_id == message.from_user.id))).scalar_one_or_none()
        if user is None:
            user = User(
                telegram_id=message.from_user.id,
                telegram_username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name,
                locale="ru" if (message.from_user.language_code or "").startswith("ru") else "uz",
            )
            db.add(user)
        else:
            # (M-06) Telegram OMITS `username` for accounts that have none, so an
            # unconditional write turned "this update carries no handle" into
            # "this account has no handle" and erased the only string support can
            # search a person by. A handle we once knew is better than none, and
            # the next update from a user who really did drop theirs still
            # carries no replacement to write — so absence can never be the
            # instruction. `first_name` is always present on a real user, and a
            # cleared `last_name` IS a value the user chose, so both stay unconditional.
            if message.from_user.username is not None:
                user.telegram_username = message.from_user.username
            user.first_name = message.from_user.first_name
            user.last_name = message.from_user.last_name
        user.last_seen_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(user)
        return user


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject | None = None) -> None:
    # (#101) Escape the Telegram first_name before it goes into an HTML message.
    name = html.escape(message.from_user.first_name) if (message.from_user and message.from_user.first_name) else "do'st"
    user = await _ensure_user(message)

    # Deep-link routing: /start arg → invitation / referral / unknown.
    #
    # `demo_<slug>` used to redeem a stored intent here and provision a tenant with
    # a trial subscription. That funnel is LATER (app/core/scope.py), so the branch
    # and the intent store behind it are gone: a `demo_` link — live, expired or
    # invented — now falls through to the ordinary welcome, creating nothing.
    start_arg = command.args if command else None

    # 1) Invitation: /start inv_<token> → open the JoinPage Mini-App.
    #    This is the fallback for clients that don't honour `startapp=`
    #    (older Telegram versions). Modern clients open the Mini-App
    #    directly without ever firing this handler.
    if user and start_arg and (
        start_arg.startswith(invite_codes.LEGACY_PREFIXES)
        or invite_codes.looks_like_code(start_arg)
    ):
        # Bare 6-char codes are what we mint now (#12); the `inv_` / `join_`
        # prefixes are only still accepted so links already forwarded in chats
        # keep working until they expire.
        token = invite_codes.strip_legacy_prefix(start_arg)
        url = f"{settings.frontend_url.rstrip('/')}/i/{token}"
        await message.answer(
            "Sizga taklif yuborilgan. Pastdagi tugmani bosing 👇",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="📝 Forma to'ldirish", web_app=WebAppInfo(url=url)),
            ]]),
        )
        return

    # 2) Referral capture: /start ref_<code> → stamp who referred this user (#196).
    #    No tenant exists yet, so the Referral row + any reward are materialised
    #    later (at the referee's first paid subscription). NO early return — the
    #    referred user still falls through to the standard welcome + WebApp button.
    if user and start_arg and start_arg.startswith("ref_"):
        async with worker_session() as db:
            db_user = (await db.execute(
                select(User).where(User.telegram_id == user.telegram_id)
            )).scalar_one()
            if await record_referral_intent(db, db_user, start_arg.removeprefix("ref_")):
                await db.commit()

    text = (
        f"Assalomu alaykum, <b>{name}</b>!\n\n"
        "Men <b>Muddato</b> — bo'lib to'lashlarni boshqarish CRM'ining yordamchi botiman.\n\n"
        "• Mijoz bo'lsangiz: keyingi to'lovingizni shu yerda ko'rasiz va chek yuborasiz.\n"
        "• Biznes egasi yoki xodim bo'lsangiz: WebApp orqali to'liq paneliga kiring.\n\n"
        "Boshlash uchun pastdagi tugmani bosing 👇"
    )
    await message.answer(text, reply_markup=_open_webapp_keyboard())


#: One localized sentence per outcome. The bot answers in the person's own
#: language and never echoes the number back — a chat is a shared surface and a
#: confirmation does not need to restate what was just shared.
_CONTACT_REPLY: Final[dict[str, dict[str, str]]] = {
    "uz": {
        "VERIFIED": "✅ Telefon raqamingiz tasdiqlandi. Endi Muddato'ni ochishingiz mumkin.",
        "UNCHANGED": "✅ Telefon raqamingiz allaqachon tasdiqlangan.",
        "CONTACT_NOT_OWNER": "Bu raqam sizga tegishli emas. Iltimos, o'z raqamingizni ulashing.",
        "CONTACT_MALFORMED": "Raqamni o'qib bo'lmadi. Qaytadan urinib ko'ring.",
        "CONTACT_CONFLICT": "Hisobingizda boshqa raqam tasdiqlangan. Yordam uchun murojaat qiling.",
        "CONTACT_PHONE_TAKEN": "Bu raqam boshqa hisobga biriktirilgan. Yordam uchun murojaat qiling.",
        "CONTACT_UNKNOWN_SENDER": "Avval /start buyrug'ini yuboring.",
        "UNSUPPORTED_REGION": "Hozircha faqat O'zbekiston (+998) raqamlari qo'llab-quvvatlanadi.",
        "SUPPORT_REQUIRED": "Ba'zi yozuvlarni ulash uchun qo'llab-quvvatlash xizmati kerak.",
    },
    "ru": {
        "VERIFIED": "✅ Ваш номер подтверждён. Теперь можно открыть Muddato.",
        "UNCHANGED": "✅ Ваш номер уже подтверждён.",
        "CONTACT_NOT_OWNER": "Это не ваш номер. Пожалуйста, поделитесь своим.",
        "CONTACT_MALFORMED": "Не удалось прочитать номер. Попробуйте ещё раз.",
        "CONTACT_CONFLICT": "В аккаунте уже подтверждён другой номер. Обратитесь в поддержку.",
        "CONTACT_PHONE_TAKEN": "Этот номер привязан к другому аккаунту. Обратитесь в поддержку.",
        "CONTACT_UNKNOWN_SENDER": "Сначала отправьте команду /start.",
        "UNSUPPORTED_REGION": "Пока поддерживаются только номера Узбекистана (+998).",
        "SUPPORT_REQUIRED": "Для части записей потребуется помощь поддержки.",
    },
}


@router.message(F.contact)
async def on_contact(message: Message) -> None:
    """A shared contact — the only in-Telegram proof of who owns a number.

    `initData` never carries a phone, so this update is the entire evidence
    chain. It is accepted only when Telegram says the shared card belongs to the
    sender; see `services.telegram_contact` for why that one field decides it.
    """
    from app.core.phone_identity import PhoneEligibility
    from app.services.telegram_contact import ContactOutcome, record_verified_contact

    if message.from_user is None or message.contact is None:
        return
    await _ensure_user(message)

    async with worker_session() as db:
        result = await record_verified_contact(
            db,
            sender_telegram_id=message.from_user.id,
            contact_user_id=message.contact.user_id,
            raw_phone=message.contact.phone_number,
        )
        sweep = None
        if result.ok:
            # The proof arrived in a private chat with no shop attached to it.
            # Somewhere a merchant typed this number into their books days ago
            # and the app has been calling the person unconnected ever since —
            # so the moment ownership is established, go and connect them.
            from app.services.identity_link import reconcile_all_tenants
            user = (await db.execute(
                select(User).where(User.telegram_id == message.from_user.id)
            )).scalar_one()
            sweep = await reconcile_all_tenants(db, user=user)
            await db.commit()
        else:
            await db.rollback()

    lang = "ru" if (message.from_user.language_code or "").startswith("ru") else "uz"
    key = result.outcome.value
    # An accepted proof for a number outside the served region is still a
    # successful verification — the account keeps it and is told why it cannot
    # activate, rather than being refused or, worse, removed.
    if result.ok and result.eligibility is PhoneEligibility.unsupported_region:
        key = "UNSUPPORTED_REGION"
    text = _CONTACT_REPLY[lang].get(key) or _CONTACT_REPLY[lang][ContactOutcome.malformed.value]
    # A generic outcome only. How many shops this person deals with, and which,
    # is not something a bot reply may enumerate.
    if sweep is not None and sweep.needs_operator:
        text = f"{text}\n\n{_CONTACT_REPLY[lang]['SUPPORT_REQUIRED']}"
    await message.answer(text, reply_markup=_open_webapp_keyboard())


@router.message(Command("login"))
async def cmd_login(message: Message) -> None:
    await message.answer(
        "Telegram orqali hisobingizga kiring:",
        reply_markup=_login_inline(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "Komandalar:\n"
        "/start — botni boshlash\n"
        "/login — hisobga kirish\n"
        "/help — yordam"
    )


@router.message(F.text)
async def fallback(message: Message) -> None:
    await message.answer(
        "Iltimos, pastdagi <b>Muddato'ni ochish</b> tugmasini bosing.",
        reply_markup=_open_webapp_keyboard(),
    )


async def setup_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start",  description="Boshlash"),
            BotCommand(command="login",  description="Hisobga kirish"),
            BotCommand(command="help",   description="Yordam"),
        ],
        scope=BotCommandScopeDefault(),
    )


def build_bot() -> Bot:
    return Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(router)
    return dp
