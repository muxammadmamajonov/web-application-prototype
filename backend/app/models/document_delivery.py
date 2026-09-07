"""Generated documents that were sent, or archived for reuse.

Two jobs kept apart by ``purpose``:

``user_delivery``
    INVARIANT: at most one live row per (person, document, content version).
    The unique index is the arbiter — concurrent requests both read "nothing
    sent yet".

``archive``
    INVARIANT: a ``file_id`` is bot-scoped, so ``bot_owner`` is part of the
    key. Keyed on content version too, so a changed document is never served
    from the cache of an older one.

INVARIANT: every row is disposable. Documents regenerate from ``installments``
and ``payments``; Telegram is never the system of record.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

from .base import TimestampMixin, uuid_pk

#: A delivery in flight, finished, or failed. `sending` exists so a concurrent
#: second request can SEE the first one rather than starting its own.
DELIVERY_STATES = ("sending", "sent", "failed")

PURPOSES = ("user_delivery", "archive")


class DocumentDelivery(Base, TimestampMixin):
    __tablename__ = "document_deliveries"

    id: Mapped[uuid_pk]

    purpose: Mapped[str] = mapped_column(String(16), nullable=False)

    #: Which document. `kind` matches the download grant's vocabulary
    #: (contract / payment_receipt / invoice) and `object_id` is that row's id.
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)

    #: A fingerprint of the CONTENT, so a regenerated document that differs is
    #: never served from a cache keyed only on identity.
    document_version: Mapped[str] = mapped_column(String(64), nullable=False)

    #: Who received it. NULL for an archive row, which belongs to no user.
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
    )
    #: The owning shop, for isolation and for choosing the archive topic.
    #: NULL is platform-owned (the General topic).
    tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True,
    )

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="sending")

    # ── the Telegram reference ────────────────────────────────────────────────
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    message_thread_id: Mapped[int | None] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    #: Bot-specific download handle. Meaningless to any other bot, which is why
    #: `bot_owner` travels with it rather than being assumed.
    file_id: Mapped[str | None] = mapped_column(String(256))
    file_unique_id: Mapped[str | None] = mapped_column(String(64))
    bot_owner: Mapped[str] = mapped_column(String(16), nullable=False, default="platform")

    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "purpose IN ('user_delivery', 'archive')", name="ck_docdel_purpose",
        ),
        CheckConstraint(
            "status IN ('sending', 'sent', 'failed')", name="ck_docdel_status",
        ),
        # An archive row has no user; a user delivery must have one. Without
        # this the two purposes could quietly blur into each other and the
        # partial indexes below would stop meaning what they say.
        CheckConstraint(
            "(purpose = 'archive' AND user_id IS NULL) OR "
            "(purpose = 'user_delivery' AND user_id IS NOT NULL)",
            name="ck_docdel_user_matches_purpose",
        ),
        # ONE delivery of one document to one person. This is the idempotency,
        # and it is here rather than in application code because two concurrent
        # requests can both read "nothing sent yet" — only the database can
        # arbitrate. A failed attempt is excluded so a genuine retry is allowed.
        # One live delivery per person per document VERSION. Without the
        # version a regenerated contract with different terms could never reach
        # somebody who already had the superseded one.
        Index(
            "uq_docdel_user_object",
            "user_id", "kind", "object_id", "document_version",
            unique=True,
            postgresql_where=text("purpose = 'user_delivery' AND status <> 'failed'"),
        ),
        # One archived copy per document VERSION per bot. `bot_owner` is part of
        # the key because a `file_id` minted by one bot is not resolvable by
        # another — reusing it across bots yields "wrong file identifier".
        Index(
            "uq_docdel_archive",
            "kind", "object_id", "document_version", "bot_owner",
            unique=True,
            postgresql_where=text("purpose = 'archive' AND status = 'sent'"),
        ),
        Index("ix_docdel_lookup", "kind", "object_id"),
    )
