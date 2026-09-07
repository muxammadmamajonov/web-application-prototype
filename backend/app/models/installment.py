"""Installment plans + their per-month schedule items."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ColumnElement,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.currency import SUPPORTED_CURRENCIES
from app.core.identifiers import IdentifierType
from app.db import Base

from .base import TimestampMixin, dt_aware, money, uuid_pk
from .enums import InstallmentStatus, pg_enum

#: Rendered into the CHECK constraints on both `installments` and `payments`, so
#: the model schema and migration `ra0currency` cannot disagree about the
#: allowlist.
_CURRENCY_TUPLE_SQL = "(" + ", ".join(f"'{c}'" for c in SUPPORTED_CURRENCIES) + ")"

#: Same idea for the line-item identifier kinds.
_IDENTIFIER_TYPE_SQL = "(" + ", ".join(f"'{t.value}'" for t in IdentifierType) + ")"

if TYPE_CHECKING:
    from .customer import Customer
    from .payment import Payment
    from .product import Product
    from .tenant import Tenant
    from .user import User


class Installment(Base, TimestampMixin):
    __tablename__ = "installments"

    id: Mapped[uuid_pk]
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    customer_id: Mapped[UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    sold_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    reference: Mapped[str | None] = mapped_column(String(32), index=True)  # human-friendly code
    # Unique contract number (auto-generated like "MDD-2025-000123"). Used on
    # printed contracts and SMS notifications.
    contract_number: Mapped[str | None] = mapped_column(String(40), index=True)

    status: Mapped[InstallmentStatus] = mapped_column(
        pg_enum(InstallmentStatus, "installment_status"),
        default=InstallmentStatus.draft, nullable=False, index=True,
    )

    # The currency this contract was WRITTEN in, snapshot at creation
    # from the shop's settings. Deliberately has no Python-side default: a
    # creation path that forgets to snapshot must fail loudly at flush rather
    # than quietly mint a UZS contract for a USD shop.
    currency: Mapped[str] = mapped_column(String(8), nullable=False)

    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    down_payment: Mapped[money]
    principal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    interest_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))   # 0.0250 = 2.5 %
    # Per-installment calc overrides (default comes from TenantSettings).
    calc_formula: Mapped[str] = mapped_column(String(24), default="none", nullable=False)
    markup_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    late_fee_pct_per_day: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))

    months: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    paid_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    paid_amount: Mapped[money]
    # (#108) Early-payoff discount recorded when the customer initiates a payoff.
    # While set, the loan is considered settled once
    # ``paid_amount >= principal - payoff_discount_amount`` — otherwise paying the
    # discounted quote would never close the loan and the customer would keep
    # getting dunned for the discount amount.
    payoff_discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))

    # (#119) Reschedule re-anchor. A reschedule changes ``monthly_amount`` mid-loan;
    # ``recompute_state`` counts future progress from the amount/count settled at
    # the reschedule so the already-paid installments aren't re-interpreted at the
    # new monthly (which corrupted ``paid_count``). NULL = never rescheduled.
    resched_base_paid: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    resched_base_count: Mapped[int | None] = mapped_column(Integer)
    # First due date agreed at the latest reschedule. Future payments advance
    # from this anchor instead of snapping back to the original start_date.
    resched_base_due_date: Mapped[date | None] = mapped_column(Date)

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    next_due_date: Mapped[date | None] = mapped_column(Date, index=True)
    closed_at: Mapped[dt_aware]

    notes: Mapped[str | None] = mapped_column(String(1500))

    # (F-12) Client-supplied idempotency key for contract creation. A double-tap or
    # network retry that carries the same key collides on uq_installments_idem, so
    # the create endpoint returns the existing contract instead of issuing a
    # duplicate — a DB-enforced backstop behind the client-side submit guard.
    idempotency_key: Mapped[str | None] = mapped_column(String(128))

    tenant: Mapped[Tenant] = relationship(back_populates="installments")
    customer: Mapped[Customer] = relationship(back_populates="installments")
    sold_by: Mapped[User | None] = relationship()
    items: Mapped[list[InstallmentItem]] = relationship(
        back_populates="installment", cascade="all, delete-orphan",
    )
    # Two foreign-key paths reach this table — `installment_id` alone, and the
    # composite `(installment_id, currency)` that pins a payment to its
    # contract's currency — so the join must be stated explicitly. It uses the
    # id edge only: currency is copied at creation, never synced through the
    # relationship.
    payments: Mapped[list[Payment]] = relationship(
        back_populates="installment", cascade="all, delete-orphan",
        primaryjoin="Installment.id == Payment.installment_id",
        foreign_keys="Payment.installment_id",
    )

    __table_args__ = (
        # Hot list query: WHERE tenant_id=? [AND status=?] ORDER BY next_due/created.
        Index("ix_installments_tenant_status_due", "tenant_id", "status", "next_due_date"),
        Index("ix_installments_tenant_created", "tenant_id", "created_at"),
        # (MUDD-134) The category-analytics date-range filter reads by sale date,
        # not creation date — see alembic r99anlytidx.
        Index("ix_installments_tenant_start_date", "tenant_id", "start_date"),
        # Unindexed FK → parent (user) delete would seq-scan installments.
        Index("ix_installments_sold_by", "sold_by_user_id"),
        # Contract numbers are unique per tenant (NULL drafts allowed).
        Index(
            "uq_installments_tenant_contract", "tenant_id", "contract_number",
            unique=True, postgresql_where=text("contract_number IS NOT NULL"),
        ),
        # (F-12) At most one contract per (tenant, idempotency_key) — a replayed
        # create collides here and the endpoint returns the existing contract.
        Index(
            "uq_installments_idem", "tenant_id", "idempotency_key",
            unique=True, postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        # Money invariants — an app bug can't persist impossible balances.
        CheckConstraint(
            "total_amount >= 0 AND principal >= 0 AND down_payment >= 0 "
            "AND monthly_amount >= 0 AND paid_amount >= 0",
            name="amounts_nonneg",
        ),
        CheckConstraint("months > 0", name="months_pos"),
        CheckConstraint("down_payment <= total_amount", name="down_le_total"),
        # Only currencies the product can actually hold.
        CheckConstraint(
            "currency IN " + _CURRENCY_TUPLE_SQL, name="ck_installments_currency",
        ),
        # Redundant against the primary key on purpose: it is the target
        # `payments (installment_id, currency)` references, which is what makes
        # "a payment is in its contract's currency" an invariant of the database
        # instead of a convention of the code.
        UniqueConstraint("id", "currency", name="uq_installments_id_currency"),
    )

    @hybrid_property
    def remaining_balance(self) -> Decimal:
        """Amount still owed on this contract: ``principal - paid_amount``.

        (#876) The single source of truth for "remaining" money math, previously
        re-derived inline at ~11 call sites (me / tenant / scheduler / reschedule /
        payme) plus a SQL aggregate — each one a chance to drift. As a
        ``hybrid_property`` it works both per-instance in Python (``inst.remaining_balance``,
        null-guarded for un-flushed instances) and inside a query
        (``func.sum(Installment.remaining_balance)``), so both layers share one definition.

        This is the GROSS balance and deliberately does NOT subtract any
        ``payoff_discount_amount`` (preserving the prior inline behaviour). The loan
        *settles* at ``principal - payoff_discount_amount`` (see
        ``installments.recompute_state``), and that discount is transient (only set
        mid-payoff), so the two agree in normal display. Centralising here means a
        discount-aware or ``max(0, …)`` variant is now a one-line change, not eleven.
        """
        return (self.principal or Decimal("0")) - (self.paid_amount or Decimal("0"))

    @remaining_balance.inplace.expression
    @classmethod
    def _remaining_balance_expr(cls) -> ColumnElement[Decimal]:
        # Both columns are NOT NULL (see the amounts_nonneg CHECK), so the SQL form
        # needs no COALESCE — it mirrors the Python branch exactly.
        return cls.principal - cls.paid_amount


class InstallmentItem(Base, TimestampMixin):
    __tablename__ = "installment_items"

    id: Mapped[uuid_pk]
    installment_id: Mapped[UUID] = mapped_column(
        ForeignKey("installments.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    product_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"),
    )
    product_name_snapshot: Mapped[str] = mapped_column(String(160), nullable=False)
    # Reference to the global catalog model the tenant picked (nullable so a
    # tenant can still record a sale of an off-catalog item).
    catalog_model_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("catalog_models.id", ondelete="SET NULL"),
    )
    # Human-readable variant label (e.g. "256GB Black"). Denormalised from
    # the picker so reports survive future template edits.
    variant_label: Mapped[str | None] = mapped_column(String(120))
    serial_number: Mapped[str | None] = mapped_column(String(64))
    # What `serial_number` MEANS on this line — a device IMEI, the shop's own
    # serial/contract reference, or a value recorded before the distinction
    # existed. Validation differs per kind, so a value without one cannot be
    # checked at all. Paired with `serial_number` by a CHECK constraint.
    identifier_type: Mapped[str | None] = mapped_column(String(24))
    # (MUDD-83) What KIND of thing this line sold — a `catalog_categories.slug`
    # such as `smartfonlar` or `avtomobillar`.
    #
    # A SNAPSHOT, deliberately: a plain string, not a foreign key. Catalog rows
    # get renamed, re-slugged, merged and deleted; a contract from two years
    # ago still has to be able to say what was sold under it. `product_id` and
    # `catalog_model_id` are both `ON DELETE SET NULL` for the same reason
    # `product_name_snapshot` exists beside `product_id` — the references are
    # convenience, the snapshot is the record.
    #
    # Nullable, and it stays nullable. Rows written before this column existed,
    # and free-text lines with neither a product nor a catalog model, genuinely
    # have no category; inventing one would be worse than admitting it.
    category: Mapped[str | None] = mapped_column(String(64))
    qty: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    installment: Mapped[Installment] = relationship(back_populates="items")
    product: Mapped[Product | None] = relationship()
    # (MUDD-85) Category-linked identifier fields (IMEI 1/2, VIN, …), one row
    # per (item, key) — see `InstallmentItemIdentifier` for why this is a
    # child table rather than another JSONB column.
    identifiers: Mapped[list[InstallmentItemIdentifier]] = relationship(
        back_populates="item", cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_items_product", "product_id"),
        Index("ix_items_catalog_model", "catalog_model_id"),
        # (MUDD-83) The list endpoint filters installments by the category of
        # their items, via EXISTS over this table, and the overview aggregates
        # by it. Both read `category` for a set of installment_ids, so lead the
        # index with `installment_id` — a lone `category` index would still
        # make the join scan.
        Index("ix_items_installment_category", "installment_id", "category"),
        # The duplicate warning scans by value within a tenant.
        Index("ix_items_serial_number", "serial_number",
              postgresql_where=text("serial_number IS NOT NULL")),
        CheckConstraint("qty > 0 AND unit_price >= 0 AND line_total >= 0", name="qty_price_valid"),
        CheckConstraint(
            "identifier_type IS NULL OR identifier_type IN " + _IDENTIFIER_TYPE_SQL,
            name="identifier_type",
        ),
        # Both or neither: a type with no value says nothing, and a value with
        # no type is exactly the ambiguity this column removes.
        CheckConstraint(
            "(NULLIF(btrim(serial_number), '') IS NOT NULL) = (identifier_type IS NOT NULL)",
            name="identifier_paired",
        ),
    )


class InstallmentItemIdentifier(Base, TimestampMixin):
    """One category-schema field value on one contract line (MUDD-85).

    A CHILD TABLE rather than a JSONB column on `InstallmentItem`, for one
    reason: tenant-wide uniqueness. `catalog_categories.identifier_schema`
    keys are dynamic per category (`imei1`, `vin`, …), so a static partial
    unique index on a fixed set of JSONB expressions cannot express "this
    value must be unique among all values ever stored under this key for this
    tenant" the way `uq_item_identifier_tenant_key_value` below can — the
    database, not just the API's pre-check, refuses a duplicate IMEI.

    Distinct from `InstallmentItem.serial_number` / `identifier_type` (a
    single free-text field the seller types on ANY line, category or not).
    This table only ever holds values whose key was declared by the item's
    OWN resolved category at write time (`app.core.category_identifiers`) —
    it is not a place to stash arbitrary key/value pairs.
    """
    __tablename__ = "installment_item_identifiers"

    id: Mapped[uuid_pk]
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    item_id: Mapped[UUID] = mapped_column(
        ForeignKey("installment_items.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    # A `catalog_categories.identifier_schema[].key`, e.g. "imei1" or "vin" —
    # a snapshot of the schema key at submission time, not a live reference:
    # renaming a category's field later must not orphan history already sold
    # under the old name (same reasoning as `InstallmentItem.category`).
    key: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[str] = mapped_column(String(64), nullable=False)

    item: Mapped[InstallmentItem] = relationship(back_populates="identifiers")

    __table_args__ = (
        # THE ENFORCEMENT. A shop cannot record the same IMEI twice under the
        # same field across any of its contracts — regardless of which item or
        # installment it lives on.
        UniqueConstraint(
            "tenant_id", "key", "value", name="uq_item_identifier_tenant_key_value",
        ),
    )
