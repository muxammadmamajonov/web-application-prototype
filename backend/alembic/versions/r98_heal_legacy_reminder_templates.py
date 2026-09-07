"""Heal reminder templates seeded with placeholders the send path never supplied (MUDD-111)

The owner reported this exact message reaching a real customer:

    Salom, Muxammad! {days_left} kun ichida 4800000.00 UZS to'lash kerak.
    Shartnoma: {contract}

Three defects in one line, all traceable to a single rival copy of the default
templates in ``app/api/tenant_settings.py::_get_or_create_settings``:

* ``{days_left}`` / ``{contract}`` — names the 09:00 cron's context never
  contained, so ``render()`` left them literal;
* ``4800000.00`` — the raw ``str(Decimal)`` the cron passed;
* a hardcoded ``UZS`` — so a shop trading in USD dunned in the wrong unit.

The code fix removes that rival dict (the seed now comes from the one canonical
set in ``app/services/scheduler.py::_DEFAULTS_UZ``) and routes every caller
through one context builder. That fixes tenants created from now on. It does
nothing for the rows already written — which is what this migration is for.

Scope, deliberately narrow
--------------------------
Only templates that are **byte-identical to the bad seed** are replaced. A shop
that edited its own copy keeps it, even if the edit still contains
``{days_left}`` — rewriting an owner's own words is not a migration's business,
and the code fix now resolves those aliases at render time anyway, so their
template works either way.

Matching on the exact seed string is what makes this safe to run twice: after
the first pass no row matches, so ``upgrade()`` is idempotent by construction.

Irreversible by design
----------------------
``downgrade()`` is a no-op. Restoring the broken strings would re-introduce the
customer-visible defect this exists to remove, and the pre-migration value of an
untouched default is knowable from this file rather than needing to be stored.

Scale note (MUDD-123/L1)
------------------------
``upgrade()`` reads every non-empty ``tenant_settings.reminder_templates`` into
memory in one ``fetchall()`` and issues one ``UPDATE`` per healed tenant — no
batching, no server-side cursor. That is fine at the current tenant count
(hundreds), and deliberately simple: the match is a byte comparison Python does
and SQL cannot express against a JSONB value without repeating the seed text in
the query, which is the rival-copy problem this migration exists to end. If
``tenant_settings`` ever reaches the scale where this matters, the shape to
move to is a keyset-paged read plus ``executemany`` over the patched rows — not
a rewrite of this file, which has already run in production.

Revision ID: r98healtpl
Revises: r97catmodels
Create Date: 2026-09-01 18:00:00+00:00
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "r98healtpl"
down_revision = "r97catmodels"
branch_labels = None
depends_on = None


#: The exact strings the removed seed wrote. Kept verbatim — this migration
#: identifies rows by equality with them, so a "tidy-up" of the quoting here
#: would silently make it match nothing.
_BAD_SEED = {
    "before_due": "Salom, {customer_name}! {days_left} kun ichida {amount} UZS to'lash kerak. Shartnoma: {contract}",
    "due_today":  "Salom! Bugun to'lov sanasi: {amount} UZS. Chekni botga yuboring.",
    "overdue":    "Eslatma: {days_overdue} kun kechikdi. To'lash kerak: {amount} UZS.",
    "owner_overdue_alert": "{customer_name} ({phone}) {days_overdue} kun kechikdi. Shartnoma {contract}, qarz: {amount} UZS",
}


def _canonical() -> dict[str, str]:
    """The canonical defaults, read from the application rather than copied.

    A migration that pasted its own copy of the templates would become the
    FOURTH rival source of this text — the precise failure mode being fixed
    here. Importing keeps one source of truth; if the import is impossible in
    the environment running alembic, the migration refuses to guess.

    (MUDD-123/L1) "Refuses to guess" means *says so*. The import is on
    `app.services.scheduler`, which pulls in a large part of the application —
    a missing optional dependency or a partially-installed image raises
    `ImportError` here and, unwrapped, aborts the ENTIRE `alembic upgrade` with
    a traceback that points at a scheduler module rather than at the migration
    that needs it. Wrapped, the operator gets the one sentence that matters.
    """
    try:
        from app.services.scheduler import default_reminder_templates
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "r98healtpl needs app.services.scheduler.default_reminder_templates to "
            "read the canonical reminder templates, and it could not be imported "
            f"({exc}). This migration deliberately keeps no second copy of that "
            "text. Run alembic from an environment where the application package "
            "imports (the API image), then re-run the upgrade."
        ) from exc

    return default_reminder_templates()


def upgrade() -> None:
    canonical = _canonical()
    conn = op.get_bind()

    rows = conn.execute(sa.text(
        "SELECT tenant_id, reminder_templates FROM tenant_settings "
        "WHERE reminder_templates IS NOT NULL AND reminder_templates <> '{}'::jsonb"
    )).fetchall()

    healed = 0
    for tenant_id, templates in rows:
        if not isinstance(templates, dict):
            continue
        patched = dict(templates)
        changed = False
        for key, bad in _BAD_SEED.items():
            if patched.get(key) == bad and canonical.get(key):
                patched[key] = canonical[key]
                changed = True
        if not changed:
            continue
        conn.execute(
            sa.text(
                "UPDATE tenant_settings SET reminder_templates = CAST(:tpl AS jsonb) "
                "WHERE tenant_id = :tid"
            ),
            {"tpl": json.dumps(patched, ensure_ascii=False), "tid": tenant_id},
        )
        healed += 1

    print(f"[r98healtpl] healed reminder_templates for {healed} tenant(s)")


def downgrade() -> None:
    """No-op — see the module docstring."""
