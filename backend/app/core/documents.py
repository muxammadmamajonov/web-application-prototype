"""Money, dates and enums as a customer-facing document writes them.

INVARIANT: every customer-facing document formats through here and nowhere
else. That is what keeps a contract and its receipt from writing the same
number two different ways.

INVARIANT: this module renders, it never computes. Nothing here rounds, sums or
converts a financial value — quantization is presentational and is never
written back.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

#: The business timezone. Every stored timestamp is UTC; every timestamp a
#: person reads is this. Shared with `app/core/business_time.py`, which owns the
#: scheduler's calendar — the two must not drift apart.
TASHKENT = ZoneInfo("Asia/Tashkent")

#: Document locales. Not the same set as the UI's: a document is issued in one
#: language and keeps it, so there is no "browser default" here.
Locale = str
DEFAULT_LOCALE: Locale = "uz"
SUPPORTED_LOCALES: tuple[Locale, ...] = ("uz", "ru")


def normalize_locale(locale: str | None) -> Locale:
    """Fold anything the caller has (``ru-RU``, ``None``, ``de``) to a locale we
    actually have copy for. Falls back rather than raising: a document must
    render even when the preference is junk."""
    base = (locale or "").split("-")[0].lower()
    return base if base in SUPPORTED_LOCALES else DEFAULT_LOCALE


# ── money ───────────────────────────────────────────────────────────────────

#: Currencies written without a fractional part. So'm banknotes have no
#: sub-unit in circulation, so `4 800 000,00` is noise on a receipt — and worse,
#: it invites the reader to wonder what the `.00` is hiding.
_ZERO_DECIMAL = frozenset({"UZS"})

#: A narrow no-break space between groups. A plain space lets a line break fall
#: inside a number ("4 800\n000"), and a comma collides with the decimal comma
#: used in both ru and uz.
_GROUP_SEP = " "


def money(amount: Decimal | int | float | str | None, currency: str | None = None) -> str:
    """A number as a person reads it: grouped thousands, correct decimals.

    `4800000.00` → `4 800 000` for UZS, `4 800 000,00` for USD. The decimal
    policy follows the CURRENCY, not the caller's preference, because the same
    contract must not print two different ways in two documents.

    Quantization is presentational only — it rounds half-up to the currency's
    own precision for display and never writes the result back anywhere.
    """
    if amount is None:
        return ""
    try:
        value = Decimal(str(amount))
    except Exception:
        # A malformed value must not take down a whole contract render. Show
        # what we were given rather than a stack trace on a customer's document.
        return str(amount)

    code = (currency or "").upper()
    places = 0 if (not code or code in _ZERO_DECIMAL) else 2
    quantum = Decimal(1) if places == 0 else Decimal("0.01")
    from decimal import ROUND_HALF_UP
    value = value.quantize(quantum, rounding=ROUND_HALF_UP)

    negative = value < 0
    digits = f"{abs(value):.{places}f}"
    whole, _, frac = digits.partition(".")

    grouped = ""
    for i, ch in enumerate(reversed(whole)):
        if i and i % 3 == 0:
            grouped = _GROUP_SEP + grouped
        grouped = ch + grouped

    out = grouped if places == 0 else f"{grouped},{frac}"
    return f"-{out}" if negative else out


def money_with_currency(
    amount: Decimal | int | float | str | None,
    currency: str | None,
    locale: str | None = None,
) -> str:
    """``4 800 000 so'm``. The currency label is localized; the number is not."""
    text = money(amount, currency)
    if not text:
        return ""
    return f"{text} {currency_label(currency, locale)}".strip()


_CURRENCY_LABELS: dict[str, dict[str, str]] = {
    "uz": {"UZS": "so'm", "USD": "USD", "EUR": "EUR", "RUB": "RUB"},
    "ru": {"UZS": "сум", "USD": "USD", "EUR": "EUR", "RUB": "RUB"},
}


def currency_label(currency: str | None, locale: str | None = None) -> str:
    code = (currency or "").upper()
    if not code:
        return ""
    return _CURRENCY_LABELS[normalize_locale(locale)].get(code, code)


# ── dates ───────────────────────────────────────────────────────────────────

#: Ordinal-suffixed Uzbek months: "1-sentabr, 2026". The hyphen is part of the
#: construction, not punctuation we chose.
_MONTHS_UZ = (
    "yanvar", "fevral", "mart", "aprel", "may", "iyun",
    "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr",
)
_MONTHS_RU = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def _as_local_date(value: date | datetime) -> date:
    """A date in the business timezone.

    A naive datetime is treated as UTC — that is what the database hands back
    when a column was written without an offset, and guessing "local" instead
    would silently shift a payment across midnight.
    """
    if isinstance(value, datetime):
        from datetime import UTC
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(TASHKENT).date()
    return value


def long_date(value: date | datetime | str | None, locale: str | None = None) -> str:
    """``1-sentabr, 2026`` / ``1 сентября 2026 г.``

    Accepts an ISO string too, because half the render contexts already carry
    one; an unparseable string is returned unchanged rather than raising inside
    a template.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        # Kept under its own name: `value` is rebound to a date on the way
        # through, and the fallbacks all want the ORIGINAL string.
        text = value
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            try:
                value = date.fromisoformat(text)
            except ValueError:
                return text
    d = _as_local_date(value)
    loc = normalize_locale(locale)
    if loc == "ru":
        return f"{d.day} {_MONTHS_RU[d.month - 1]} {d.year} г."
    return f"{d.day}-{_MONTHS_UZ[d.month - 1]}, {d.year}"


def long_datetime(value: datetime | str | None, locale: str | None = None) -> str:
    """``1-sentabr, 2026, 14:23`` — the date form plus a wall clock.

    Seconds and microseconds are dropped deliberately. A receipt says when a
    payment was made, and `14:23:11.482913` claims a precision that means
    nothing to the person holding it while making the line long enough to
    collide with its own label.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            return text
    from datetime import UTC
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    local = aware.astimezone(TASHKENT)
    return f"{long_date(local, locale)}, {local:%H:%M}"


# ── enums people read ───────────────────────────────────────────────────────

_METHOD_LABELS: dict[str, dict[str, str]] = {
    "uz": {
        "cash": "Naqd", "card": "Karta", "click": "Click", "payme": "Payme",
        "transfer": "Bank o'tkazmasi", "late_fee": "Penya",
    },
    "ru": {
        "cash": "Наличные", "card": "Карта", "click": "Click", "payme": "Payme",
        "transfer": "Банковский перевод", "late_fee": "Пеня",
    },
}

_STATUS_LABELS: dict[str, dict[str, str]] = {
    "uz": {
        "pending": "Kutilmoqda", "approved": "Tasdiqlangan",
        "rejected": "Rad etilgan", "refunded": "Qaytarilgan",
    },
    "ru": {
        "pending": "Ожидает", "approved": "Подтверждён",
        "rejected": "Отклонён", "refunded": "Возвращён",
    },
}


def _label(table: dict[str, dict[str, str]], value: object, locale: str | None) -> str:
    """Look a code up, and if it is unknown show the code rather than nothing.

    A new enum member reaching a document before its copy does is a translation
    gap; rendering blank would turn it into a document that appears to say the
    payment has no status at all.
    """
    code = getattr(value, "value", value)
    code = "" if code is None else str(code)
    if not code:
        return ""
    return table[normalize_locale(locale)].get(code, code)


def payment_method_label(method: object, locale: str | None = None) -> str:
    return _label(_METHOD_LABELS, method, locale)


def payment_status_label(status: object, locale: str | None = None) -> str:
    return _label(_STATUS_LABELS, status, locale)


# ── document names ──────────────────────────────────────────────────────────

#: What a document is called in each language, used to build the filename a
#: person sees in their Telegram chat.
_DOC_NOUNS: dict[str, dict[str, str]] = {
    "uz": {"contract": "shartnoma", "payment_receipt": "chek", "invoice": "hisob-faktura"},
    "ru": {"contract": "dogovor", "payment_receipt": "chek", "invoice": "schet"},
}


def safe_filename_part(text: str | None, *, limit: int = 40) -> str:
    """Reduce arbitrary text to something safe in a filename on any platform.

    Latin letters, digits, hyphen. Everything else — including the entire
    Cyrillic and Uzbek-Latin repertoire, path separators, and the traversal
    sequences somebody will eventually try — becomes a hyphen. The result is
    only ever a *label*: no code path turns a filename into a storage path.
    """
    if not text:
        return ""
    out: list[str] = []
    for ch in text:
        if ch.isascii() and (ch.isalnum() or ch in "-_"):
            out.append(ch)
        else:
            out.append("-")
    collapsed = "-".join(filter(None, "".join(out).split("-")))
    return collapsed[:limit].strip("-")


def document_filename(kind: str, *, reference: str | None, locale: str | None = None) -> str:
    """``MDD-2026-000001-shartnoma.pdf``.

    The contract number is the whole identity of the file, which is why it is
    the stem. Nothing else about the customer goes in: a filename is displayed
    in a chat list, is quoted when the file is forwarded, and survives in a
    downloads folder — a passport number or a phone in there would outlive
    every access control the document itself has.
    """
    noun = _DOC_NOUNS[normalize_locale(locale)].get(kind, "hujjat")
    stem = safe_filename_part(reference) or "muddato"
    return f"{stem}-{noun}.pdf"
