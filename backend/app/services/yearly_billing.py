"""Yearly billing — one discount, configured once, applied everywhere.

A shop paying twelve months up front gets a percentage off. That percentage is a
COMMERCIAL LEVER, not a constant: the operator changes it for a campaign, a
season, or a competitor, and it must change on the landing page and in the
billing screen at the same moment, without a deploy.

So it lives in `platform_settings` under one key and is read by one function.
The yearly price is DERIVED from the monthly one — never stored beside it —
because two stored prices are two things to keep in step, and the moment a plan
is repriced without its yearly twin the landing page starts quoting a discount
that is not real. `docs/SOURCE_OF_TRUTH.md` has the general form of that lesson;
this is the same rule applied to money the customer actually pays.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PlatformSetting

#: The single settings row the admin form writes.
YEARLY_KEY = "yearly_billing"

#: Shipped default: twelve months for the price of ~9.6.
DEFAULT_DISCOUNT_PCT = 20
DEFAULT_ENABLED = True

#: A discount over this is almost certainly a typo (200 meant as 20), and one at
#: 100 would hand out the product for nothing. Refused on write, clamped on read
#: so a bad row already in the table cannot produce a free plan.
MAX_DISCOUNT_PCT = 90


def _whole_sum(value: Decimal) -> int:
    """Round to whole so'm, ROUND_HALF_UP — the money policy (#747).

    Not `core.money_utils.round_amount`: that rounds to 2 decimal places, and a
    published price with tiyin in it is not a price anyone can pay or print. Same
    rounding mode, different unit.
    """
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True, slots=True)
class YearlyBilling:
    enabled: bool
    discount_pct: int

    def price_for(self, monthly: int) -> int:
        """The full yearly price for a plan billed at ``monthly`` per month.

        Rounded to whole so'm — a price with a fractional so'm in it is not a
        price anyone can pay or print on an invoice.
        """
        if not self.enabled or self.discount_pct <= 0:
            return int(monthly) * 12
        gross = Decimal(int(monthly)) * 12
        net = gross * (Decimal(100 - self.discount_pct) / Decimal(100))
        return _whole_sum(net)

    def monthly_equivalent(self, monthly: int) -> int:
        """What the yearly price works out to per month — the number the pricing
        page should show under the toggle, because "1,430,400/year" means far
        less to a shop owner than "119,200/month, billed yearly"."""
        return _whole_sum(Decimal(self.price_for(monthly)) / 12)

    def saving_for(self, monthly: int) -> int:
        """Absolute so'm saved over twelve months. Concrete beats a percentage."""
        return int(monthly) * 12 - self.price_for(monthly)


def parse(value: object) -> YearlyBilling:
    """Read the stored row defensively. Anything unusable falls back to shipped
    defaults rather than disabling yearly billing or quoting a wrong discount."""
    if not isinstance(value, dict):
        return YearlyBilling(DEFAULT_ENABLED, DEFAULT_DISCOUNT_PCT)
    enabled = value.get("enabled")
    raw = value.get("discount_pct")
    try:
        pct = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        pct = DEFAULT_DISCOUNT_PCT
    pct = max(0, min(MAX_DISCOUNT_PCT, pct))
    return YearlyBilling(
        enabled=DEFAULT_ENABLED if enabled is None else bool(enabled),
        discount_pct=pct,
    )


async def get_config(db: AsyncSession) -> YearlyBilling:
    row = (await db.execute(
        select(PlatformSetting).where(PlatformSetting.key == YEARLY_KEY)
    )).scalar_one_or_none()
    return parse(row.value if row else None)
