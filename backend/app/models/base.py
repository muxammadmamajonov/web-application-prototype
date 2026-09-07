"""Shared model mixins and column helpers."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

# ---- phone identity ---------------------------------------------------------


def phone_identity_sql(column: str = "phone") -> str:
    """SQL reducing a phone column to the value two records must NOT share.

    Plain digits are not enough: a bare 9-digit local number and its 998… form
    are the same phone, so the country code is applied here, inside the index
    expression. Kept in one place because `users`, `customers` and `guarantors`
    must all agree, and r96phoneuniq's migration mirrors it exactly.

    Parameterised by column because `users` holds two phone-shaped columns — the
    typed business record and the proved one — which must reduce identically or
    their indexes would disagree about what "the same number" is.

    NOTE the wrapping parentheses. Postgres requires an expression index key to
    be parenthesised — ``CREATE INDEX … ON t (CASE … END)`` is a syntax error,
    ``ON t ((CASE … END))`` is not. SQLAlchemy passes ``text()`` through verbatim
    and adds exactly one set of parens, so the expression must carry its own or
    `Base.metadata.create_all` fails outright and takes every DB-backed test with
    it. The migration writes its own pair for the same reason.
    """
    digits = f"regexp_replace({column}, '[^0-9]', '', 'g')"
    return (
        f"(CASE WHEN length({digits}) = 9 "
        f"THEN '998' || {digits} "
        f"ELSE {digits} END)"
    )


PHONE_IDENTITY_SQL = phone_identity_sql()


def phone_identity(column):
    """The same rule as PHONE_IDENTITY_SQL, as a SQLAlchemy expression.

    Two forms are needed because they are used in different places: the raw
    string builds the `Index()` (which takes SQL text), while queries that ask
    "who else holds this number?" need something composable. They must render
    identically or the planner will not use the index for the lookup, so
    `test_phone_uniqueness.py` compares the two.
    """
    from sqlalchemy import case, func, literal

    digits = func.regexp_replace(column, "[^0-9]", "", "g")
    return case((func.length(digits) == 9, literal("998").concat(digits)), else_=digits)


# ---- column type aliases ----------------------------------------------------

uuid_pk = Annotated[
    UUID,
    mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4),
]
uuid_fk = Annotated[UUID, mapped_column(PgUUID(as_uuid=True))]

money = Annotated[Decimal, mapped_column(Numeric(14, 2), default=Decimal("0"))]
# Nullable, timezone-aware event timestamp. The app always writes UTC-aware
# datetimes; a naive column makes asyncpg reject the bind. Use this for every
# *_at event column so the whole schema is consistently tz-aware.
dt_aware = Annotated[datetime | None, mapped_column(DateTime(timezone=True))]
text_short = Annotated[str, mapped_column(String(120))]
slug_t = Annotated[str, mapped_column(String(64))]
phone_t = Annotated[str | None, mapped_column(String(32))]


# ---- mixins -----------------------------------------------------------------

class TimestampMixin:
    """`created_at` / `updated_at` columns kept in UTC."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# (#100) No SoftDeleteMixin: Muddato deliberately does NOT soft-delete. Financial
# history is preserved by FK ondelete=RESTRICT on payments/installments + status
# flags (Customer.status, *.is_active), not a deleted_at column. The mixin was
# unused dead code implying a `WHERE deleted_at IS NULL` filter no table supports.


# ---- common types used by several models -----------------------------------

telegram_id_t = Annotated[int | None, mapped_column(BigInteger, index=True, unique=False)]

__all__ = (
    "TimestampMixin",
    "dt_aware",
    "money",
    "phone_t",
    "slug_t",
    "telegram_id_t",
    "text_short",
    "uuid_fk",
    "uuid_pk",
)
