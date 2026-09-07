"""The currencies a shop may hold, and where a contract's currency comes from.

A contract's currency is SNAPSHOT at creation and immutable thereafter; a
payment copies its parent contract's. The tenant setting decides only what the
NEXT contract is written in, so changing it relabels nothing and converts
nothing.

The single place that names the allowlist and the default, so the API, the
models, the migration and the frontend cannot disagree about what is valid.
"""
from __future__ import annotations

from decimal import Decimal

#: Currencies a shop may choose. Mirrored by `SUPPORTED_CURRENCIES` in
#: `frontend/src/lib/currency.ts` and enforced by CHECK constraints in
#: migration `ra0currency`.
SUPPORTED_CURRENCIES: tuple[str, ...] = ("UZS", "USD")

#: What a shop that never chose is written in. Used for NEW contracts only —
#: never to relabel a contract that already carries one.
DEFAULT_CURRENCY = "UZS"


def normalize_currency(raw: object) -> str | None:
    """A supported currency code, or None.

    None means "not a currency we can hold", and every caller treats that as a
    refusal rather than an invitation to substitute the default.
    """
    if not isinstance(raw, str):
        return None
    code = raw.strip().upper()
    return code if code in SUPPORTED_CURRENCIES else None


def currency_for_new_contract(settings: object) -> str:
    """The currency a contract created RIGHT NOW for this shop is written in.

    Takes the tenant's settings row (or None). This is the only place the
    tenant setting may be read to decide a currency, and it is only ever
    consulted at creation time — which is what makes an existing contract
    immune to the shop changing its mind.
    """
    return normalize_currency(getattr(settings, "currency", None)) or DEFAULT_CURRENCY


# ── aggregating money that is not all in one currency (MUDD-114) ────────────
#
# A shop may change `settings.currency` at any time, and that only decides what
# the NEXT contract is written in — every existing contract keeps the currency
# it was created with. So one tenant holding both UZS and USD contracts is an
# ordinary state, not a hypothetical, and every SUM over `Installment.amount`
# or `Payment.amount` that does not GROUP BY currency is adding unlike units.
#
# There is deliberately NO conversion here. Muddato has no exchange-rate source,
# and a ledger that invented one would turn a visible wrong number into an
# invisible one. The answer to "how much is owed" is a MAP, and the callers
# render it as such.

def money_by_currency(rows: object) -> dict[str, Decimal]:
    """Fold ``(currency, amount)`` result rows into ``{currency: total}``.

    Returns ``Decimal``, not ``int`` — the reports path is Decimal end to end
    and a USD figure has cents that an int would silently drop. Callers whose
    response contract is integral (the UZS-only dashboard KPIs) narrow at the
    edge; there is one folding primitive rather than an int one and a Decimal
    one that could disagree.

    Zero totals are dropped: a currency with nothing in it is not a fact about
    the shop, and keeping it would make a single-currency tenant's response look
    mixed. Rows whose currency is NULL or unsupported fold under
    :data:`DEFAULT_CURRENCY` — a legacy row predating the currency column is UZS
    by construction (backfilled that way in `ra0currency`), and dropping it
    would understate a real debt.
    """
    out: dict[str, Decimal] = {}
    for currency, amount in rows:  # type: ignore[attr-defined]
        code = normalize_currency(currency) or DEFAULT_CURRENCY
        out[code] = out.get(code, Decimal(0)) + Decimal(amount or 0)
    return {code: total for code, total in out.items() if total}


def primary_total(totals: dict[str, Decimal], primary: str) -> Decimal:
    """The one figure a single-currency screen may show, in ``primary``.

    Callers pair this with the full map. It exists so the legacy scalar fields
    (``debt_total_uzs`` and friends) can keep their key — nothing downstream
    breaks — while finally MEANING what the UI labels them with, instead of
    being a cross-currency sum wearing whichever label the client picked.
    """
    return totals.get(primary, Decimal(0))


def ints_by_currency(totals: dict[str, Decimal]) -> dict[str, int]:
    """Narrow a currency map for a response whose contract is integral."""
    return {code: int(total) for code, total in totals.items()}


async def tenant_primary_currency(db: object, tenant_id: object) -> str:
    """The currency a shop's OWN screens and reports are denominated in.

    Lives here rather than in an API module because both the API layer and
    `app.services.reports` need it, and a service importing from `app.api`
    would invert the layering (and risk an import cycle).

    This is the tenant's setting, so it is the right label for a dashboard or a
    report — and the WRONG thing to apply to an existing contract, which carries
    its own immutable currency. Callers pair it with a per-currency map: the map
    is the truth, this only decides which entry the single-figure KPI shows.
    """
    from sqlalchemy import select

    from app.models import TenantSettings

    row = (await db.execute(  # type: ignore[attr-defined]
        select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
    )).scalar_one_or_none()
    return currency_for_new_contract(row)
