"""Platform-wide provisioning defaults — read by everything that creates a shop.

WHY THIS MODULE EXISTS. "Standart tarif" (`default_plan`) is configured on
Sozlamalar → Asosiy qiymatlar and was, until now, written to a settings row that
nothing read and consumed by nothing at all:

  * the admin form saved the whole group under the key ``defaults`` but re-read
    four FLAT keys (``default_plan``, ``default_locale``, …), so the selection
    round-tripped to a row nobody looks at and the Select came back empty;
  * no provisioning path ever consulted the setting — both of them hardcoded
    ``"lite"``, a plan code the catalogue dropped in R36. Every shop provisioned
    since was stamped with a code no ``plans`` row matches, so the subscription
    insert found no plan and was skipped entirely: a tenant with no plan, no
    limits and no price.

So the value is read HERE, by one function, validated against the live
catalogue, and the fallback is the catalogue's own first active plan rather than
a literal that can rot again.

The reader tolerates BOTH shapes on purpose. Production may hold either the
group row or the older flat rows (the form has written each at different times),
and a settings value an operator can see in the UI must not depend on which era
it was saved in. The group wins where both exist; writes only ever produce the
group.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Plan, PlatformSetting

#: The one row the admin form writes. A dict of every defaults field.
DEFAULTS_KEY = "defaults"

#: Pre-group rows, still readable so an existing install keeps its configuration.
#: Scalars stored through ``PUT /platform/settings/{key}`` are wrapped as
#: ``{"v": …}`` by that endpoint, which is why unwrapping is part of reading.
LEGACY_FLAT_KEYS = ("default_plan", "default_locale", "default_timezone", "trial_days")

DEFAULT_LOCALE = "uz"
DEFAULT_TIMEZONE = "Asia/Tashkent"
DEFAULT_TRIAL_DAYS = 7


def _unwrap(value: Any) -> Any:
    """A scalar setting round-trips as ``{"v": scalar}``; hand back the scalar."""
    if isinstance(value, dict) and set(value) == {"v"}:
        return value["v"]
    return value


async def read_defaults(db: AsyncSession) -> dict[str, Any]:
    """The merged defaults group: legacy flat rows first, the group row on top."""
    rows = (await db.execute(
        select(PlatformSetting).where(
            PlatformSetting.key.in_((DEFAULTS_KEY, *LEGACY_FLAT_KEYS))
        )
    )).scalars().all()
    by_key = {r.key: r.value for r in rows}

    merged: dict[str, Any] = {}
    for k in LEGACY_FLAT_KEYS:
        if k in by_key:
            merged[k] = _unwrap(by_key[k])
    group = by_key.get(DEFAULTS_KEY)
    if isinstance(group, dict):
        merged.update({k: v for k, v in group.items() if v is not None})
    return merged


async def resolve_plan_code(db: AsyncSession, requested: str | None = None) -> str | None:
    """The plan code a newly provisioned shop should carry.

    An explicit request is honoured VERBATIM. The operator naming a plan on the
    create-shop form means it, a retired plan is a legitimate thing to name, and
    the built-in ``trial`` tier is a lifecycle rather than a catalogue row — so
    validating a named code here would quietly reroute all three. The bug this
    module exists for was never that callers could name a code; it was that the
    code nobody named was a rotting literal.

    Otherwise: the configured platform default when it names a real ACTIVE plan
    (a retired plan must not become the silent default for every future shop),
    then the catalogue's first active plan. ``None`` when the catalogue is empty
    — the caller decides whether that is fatal, because "provision onto nothing"
    means different things on a fresh install and in production.
    """
    if requested:
        return requested

    active = (await db.execute(
        select(Plan.code)
        .where(Plan.is_active.is_(True))
        .order_by(Plan.sort_order, Plan.code)
    )).scalars().all()

    configured = (await read_defaults(db)).get("default_plan")
    if isinstance(configured, str) and configured in active:
        return configured
    return active[0] if active else None


async def unknown_plan_code(db: AsyncSession, code: str | None) -> bool:
    """True when ``code`` is a non-empty string no ``plans`` row matches.

    Used to refuse the write rather than store another dangling default — the
    stored `lite` is exactly what nobody noticed for as long as writes were
    unchecked.
    """
    if not code:
        return False
    exists = (await db.execute(select(Plan.id).where(Plan.code == code))).first()
    return exists is None
