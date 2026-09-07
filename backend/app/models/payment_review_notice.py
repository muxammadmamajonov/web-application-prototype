"""Telegram messages carrying a live "review this receipt" action.

INVARIANT: `Payment.status` is authoritative. Everything here is derived
delivery state — a notice can never decide, repeat or reverse a money outcome.

INVARIANT: retirement is durable. A notice outlives the request that created it,
so a restart, deploy or Telegram outage cannot lose the work.

Holds no secret: chat and message ids identify a message, they do not grant
access to it.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

from .base import TimestampMixin, uuid_pk

#: The bounded lifecycle of one notice.
#:
#: pending    — the action is live and the payment is undecided
#: retry      — decided; an attempt failed transiently and is due again
#: finalizing — a worker holds a lease and is attempting the edit now
#: finalized  — the action is gone (terminal, success)
#: abandoned  — permanently impossible, or the retry budget is spent (terminal)
NOTICE_STATES = ("pending", "retry", "finalizing", "finalized", "abandoned")

#: States a recovery sweep may pick up.
CLAIMABLE_STATES = ("retry", "finalizing")

#: Attempts before a notice is abandoned. With the backoff below this spans
#: roughly a day, which comfortably outlasts an ordinary Telegram incident.
MAX_FINALIZE_ATTEMPTS = 8


class PaymentReviewNotice(Base, TimestampMixin):
    __tablename__ = "payment_review_notices"

    id: Mapped[uuid_pk]

    payment_id: Mapped[UUID] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )

    #: Canonical numeric ids from Telegram's own send response. A target given
    #: as "@channel" resolves to a numeric chat id here; only that id can
    #: address a later edit.
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    #: The decision to render. Recorded when the payment is decided so a later
    #: retry never has to re-derive it, and never reads it back from Telegram.
    outcome: Mapped[str | None] = mapped_column(String(16))
    locale: Mapped[str | None] = mapped_column(String(8))

    #: The safe summary this message was sent with, so a retirement can keep the
    #: customer/contract/amount context instead of erasing it. Rendered by us
    #: and stored by us — never reconstructed from Telegram content.
    body: Mapped[str | None] = mapped_column(Text)

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: When a `finalizing` lease expires and the row may be reclaimed.
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: An exception class name or Telegram error code. Never a message body, a
    #: URL, a token or a phone number.
    error: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        # One notice per message.
        Index("uq_prn_message", "chat_id", "message_id", unique=True),
        Index("ix_prn_payment_state", "payment_id", "state"),
        # The sweep's access path: due work, oldest first.
        Index("ix_prn_due", "state", "next_attempt_at"),
    )
