"""Generic tenant-media references — files stored in Telegram (#814 / #818).

A ``media_files`` row records WHERE a tenant's file lives in Telegram (chat +
topic + message + ``file_id``) plus an optional durable fallback copy
(``object_key`` / ``data``) per the storage decision in ``docs/TELEGRAM-MEDIA.md``.
The file bytes are NOT kept here unless dual-write is on; downloads go through the
authenticated ``/api/media`` proxy, which fetches from Telegram (``file_id``) and
falls back to the durable copy when Telegram is unavailable.

A NULL ``tenant_id`` is platform-owner (HQ) media → the Telegram General topic.
Generic by design: the ``entity_type`` / ``entity_id`` pair links a file to any
owning row (e.g. ``("payment_receipt", <payment_id>)``), so this is not
receipt-specific. KYC images do NOT use this table (they stay in ``KycBlob``).
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

from .base import TimestampMixin, uuid_pk


class MediaFile(Base, TimestampMixin):
    __tablename__ = "media_files"

    id: Mapped[uuid_pk]
    # NULL = platform-owner (HQ) media → Telegram General topic. CASCADE so a
    # deleted tenant's references go with it (the Telegram messages are cleaned
    # separately / left as orphan history).
    tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True,
    )

    # ── Telegram reference (the file lives here) ───────────────────────────────
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    message_thread_id: Mapped[int | None] = mapped_column(Integer)   # forum topic
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    # ``file_id`` is the bot-specific download handle; ``file_unique_id`` is the
    # cross-bot identity (useful for dedup/audit but NOT downloadable).
    file_id: Mapped[str | None] = mapped_column(String(256))
    file_unique_id: Mapped[str | None] = mapped_column(String(64), index=True)
    # Which bot owns ``file_id`` — only "platform" today (the platform bot writes
    # the shared group), but recorded so the proxy could select a different token.
    bot_owner: Mapped[str] = mapped_column(String(16), default="platform", nullable=False)

    original_filename: Mapped[str | None] = mapped_column(String(256))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(Integer)

    # ── generic owning-entity link ─────────────────────────────────────────────
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), index=True)
    uploaded_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
    )

    # pending → stored (Telegram send ok) | failed (send failed; served from the
    # durable fallback when dual-write is on).
    status: Mapped[str] = mapped_column(String(16), default="stored", nullable=False)
    error: Mapped[str | None] = mapped_column(String(500))

    # (#817) Idempotency: a server-derived digest of the logical upload
    # (entity_type + caller discriminator + content). A retry of the same upload
    # reuses the existing row instead of sending a duplicate document. NULL for
    # generic media without a key. Unique per tenant (partial index below).
    idempotency_key: Mapped[str | None] = mapped_column(String(128))

    # ── durable fallback copy (dual-write) ─────────────────────────────────────
    # Exactly one of object_key/data holds the bytes when dual-write is on; both
    # are NULL when telegram_only. Mirrors ReceiptBlob so the existing private
    # receipt store is reused as the backup.
    object_key: Mapped[str | None] = mapped_column(String(256))
    data: Mapped[bytes | None] = mapped_column(LargeBinary)

    __table_args__ = (
        Index("ix_media_files_entity", "entity_type", "entity_id"),
        # Never both fallback slots at once: dual-write populates exactly one of
        # object_key/data, telegram_only leaves both NULL. The "never both" half of
        # that invariant is enforced in the DB so a future bug can't store two
        # divergent durable copies of the same financial record.
        CheckConstraint(
            "object_key IS NULL OR data IS NULL",
            name="ck_media_files_single_blob",
        ),
        # (#817) At most one row per (tenant, idempotency_key) — the concurrency
        # backstop that keeps a double-submit from persisting two rows. Partial so
        # the many key-less generic-media rows don't collide.
        Index(
            "uq_media_files_idem", "tenant_id", "idempotency_key",
            unique=True, postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )
