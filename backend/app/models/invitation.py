"""One-shot invitation tokens for customer + staff onboarding.

Flow:
  1. Tenant clicks "Get invite link" in the dashboard.
  2. Backend creates an Invitation row with a short random token, returns
     ``https://muddato.uz/m/<tenant-slug>/i/<token>`` (or, for plans with
     `bot_mode == tenant`, the deep-link into the tenant's own bot).
  3. Recipient opens the link → Mini-App form is rendered.
  4. Recipient submits → backend marks token as used, creates the
     Customer / TenantUser, and pushes a "new customer" / "new staff"
     notification to the tenant's reminder target.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

from .base import TimestampMixin, uuid_pk

if TYPE_CHECKING:
    pass


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[uuid_pk]
    #: LEGACY plaintext, read-only. New rows leave it NULL and carry a verifier
    #: instead; a database dump must not be able to reconstruct a live link.
    token: Mapped[str | None] = mapped_column(String(40), unique=True, index=True)
    #: HMAC-SHA256 of the credential. The only thing stored for new invitations.
    token_verifier: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    # NULL for platform-level invites (kind == "platform_admin"); otherwise the
    # tenant the recipient is being onboarded into.
    tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # "customer" | "staff" | "guarantor" | "platform_admin"
    role: Mapped[str | None] = mapped_column(String(24))

    # When set, this invite is bound to a pre-created Customer (operator filled
    # all data, then handed over a one-time link). Claiming attaches the joiner's
    # Telegram id to THAT customer instead of creating a new one.
    customer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True,
    )

    # Pre-filled form data the tenant supplied when creating the invite
    # (e.g. the product they're about to sell to this customer).
    preset: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()",
    )


class TenantRole(Base, TimestampMixin):
    """Custom per-tenant role with a permissions JSON map."""

    __tablename__ = "tenant_roles"

    id: Mapped[uuid_pk]
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    slug: Mapped[str] = mapped_column(String(48), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    permissions: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(default=False, nullable=False)
