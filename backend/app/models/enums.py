"""Native Postgres ENUM types — defined once, reused across models.

Adding a value to a native PG enum (migration recipe)
-----------------------------------------------------
``ALTER TYPE ... ADD VALUE`` cannot run inside a transaction block on older
PG and Alembic autogenerate does NOT detect enum changes. To add a value:

    # in the migration, OUTSIDE the implicit transaction:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE payment_method ADD VALUE IF NOT EXISTS 'uzcard'")

Values can never be removed — to retire one, create a new type and migrate the
column. Keep the Python enum and the DB type in lockstep.
"""
from __future__ import annotations

import enum

from sqlalchemy import Enum


class TenantStatus(enum.StrEnum):
    active = "active"
    demo = "demo"
    suspended = "suspended"
    blocked = "blocked"


# PlanCode was an enum of trial/lite/pro/custom. It is gone rather than updated:
# plan codes became owner-chosen slugs in R36, so there is no closed set to
# enumerate — `Tenant.plan_code` is a String and the `plans` table is the only
# authority on which values exist. The enum had no remaining consumer and was
# already wrong (the live catalogue is starter/business/pro/enterprise/flex),
# but a stale closed set left lying around is what a reader reaches for when
# writing the next gate, which is exactly how a capability check ended up
# keyed on ("pro", "custom") and refusing the two top tiers.
#
# Gate capabilities on the Plan row's flags, never on its code. See
# tests/test_plan_gates_read_capability_flags.py.


class SubscriptionStatus(enum.StrEnum):
    trial = "trial"
    active = "active"
    past_due = "past_due"
    cancelled = "cancelled"


class InvoiceStatus(enum.StrEnum):
    """Lifecycle of a subscription invoice (Round 27)."""
    open = "open"          # issued, awaiting payment
    paid = "paid"
    overdue = "overdue"    # past due_at, unpaid → drives dunning
    void = "void"          # cancelled / superseded


class PlatformRole(enum.StrEnum):
    """Global Muddato HQ role. NULL on the User row = regular tenant user."""
    super_admin = "super_admin"
    support = "support"


class TenantRole(enum.StrEnum):
    """Role of a User inside a single Tenant."""
    owner = "owner"
    manager = "manager"
    sales = "sales"   # "Xodim · Sotuvchi"


class CustomerStatus(enum.StrEnum):
    # Customer self-onboarded via an invitation link but the tenant has
    # ``auto_approve_customers = false`` — must be approved (or rejected)
    # by an owner/manager before any installment can be issued.
    pending = "pending"
    active = "active"
    blocked = "blocked"


class InstallmentStatus(enum.StrEnum):
    draft = "draft"
    active = "active"
    overdue = "overdue"
    completed = "completed"
    cancelled = "cancelled"


class PaymentStatus(enum.StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    refunded = "refunded"


class PaymentMethod(enum.StrEnum):
    cash = "cash"
    card = "card"
    click = "click"
    payme = "payme"
    transfer = "transfer"
    # (#121) Accrued late-fee charge. Discriminates fee rows from real payments so
    # they are excluded from every collected-cash / revenue / GMV aggregation.
    late_fee = "late_fee"


class PaymentProvider(enum.StrEnum):
    click = "click"
    payme = "payme"


class BroadcastStatus(enum.StrEnum):
    draft = "draft"
    scheduled = "scheduled"
    sending = "sending"
    sent = "sent"
    failed = "failed"


class BroadcastTarget(enum.StrEnum):
    all = "all"
    active = "active"
    overdue = "overdue"
    custom = "custom"


class RiskSeverity(enum.StrEnum):
    """Severity of a cross-tenant risk signal, ascending."""
    late_30 = "late_30"
    late_60 = "late_60"
    late_90 = "late_90"
    charged_off = "charged_off"
    fraud_reported = "fraud_reported"


class RiskSignalStatus(enum.StrEnum):
    """Lifecycle of a risk signal.

    ``pending_appeal`` — auto-generated; held back for a 14-day appeal
    window before it becomes visible cross-tenant.
    ``published`` — visible to other opt-in tenants (count + severity only).
    ``withdrawn`` — source resolved (e.g. installment paid off) or revoked.
    """
    pending_appeal = "pending_appeal"
    published = "published"
    withdrawn = "withdrawn"


class RiskSignalSource(enum.StrEnum):
    auto = "auto"      # system-generated from overdue installments
    manual = "manual"  # tenant flagged the customer explicitly


class NotificationChannelType(enum.StrEnum):
    """Per-customer delivery channel for reminders (Round 25)."""
    telegram = "telegram"
    sms = "sms"
    email = "email"


class SmsStatus(enum.StrEnum):
    """Lifecycle of an outbound SMS (Eskiz.uz)."""
    pending = "pending"   # accepted by provider, awaiting delivery report
    sent = "sent"         # provider acknowledged
    failed = "failed"     # provider rejected / transport error / over budget


class StockMovementKind(enum.StrEnum):
    """Reason a product's stock changed (Round 28).

    ``qty`` on the movement row is signed by convention:
    ``in``/``return`` are positive, ``out``/``sale``/``writeoff`` negative,
    ``inventory`` is the signed delta needed to reach a counted figure.
    """
    in_ = "in"            # purchase / restock
    out = "out"           # manual outbound (non-sale)
    sale = "sale"         # auto-generated when an installment is issued
    return_ = "return"    # customer/supplier return back into stock
    writeoff = "writeoff" # damaged / lost
    inventory = "inventory"  # stock-count correction


# ---- SA Enum helpers --------------------------------------------------------

def pg_enum(py_enum: type[enum.Enum], name: str) -> Enum:
    """Native Postgres enum with stable name; values come from the enum's ``.value``."""
    return Enum(
        py_enum,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        native_enum=True,
        validate_strings=True,
    )


__all__ = (
    "BroadcastStatus",
    "BroadcastTarget",
    "CustomerStatus",
    "InstallmentStatus",
    "NotificationChannelType",
    "PaymentMethod",
    "PaymentProvider",
    "PaymentStatus",
    "PlatformRole",
    "RiskSeverity",
    "RiskSignalSource",
    "RiskSignalStatus",
    "SmsStatus",
    "StockMovementKind",
    "SubscriptionStatus",
    "TenantRole",
    "TenantStatus",
    "pg_enum",
)
