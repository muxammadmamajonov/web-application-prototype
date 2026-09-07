"""Indexed reminder dedup: notifications.installment_id FK + composite index (#75)

The reminder cron used to dedup "did we already remind about this installment
today?" with ``Notification.body LIKE '%installment=<id>%'`` — a leading-wildcard
scan that cannot use an index and grows linearly with the notifications table.

This adds a real ``notifications.installment_id`` foreign key (ondelete SET NULL,
so deleting an installment doesn't cascade-delete its notification history) plus
a composite index ``(tenant_id, kind, installment_id, created_at)`` that turns the
dedup probe into an indexed lookup. The column is nullable with no backfill —
existing rows simply have no source installment, which is correct.

Revision ID: r67notifinst
Revises: r66payoff
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "r67notifinst"
down_revision = "r66payoff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("installment_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_notifications_installment_id_installments"),
        "notifications",
        "installments",
        ["installment_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_notifications_reminder_dedup"),
        "notifications",
        ["tenant_id", "kind", "installment_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_notifications_reminder_dedup"), table_name="notifications")
    op.drop_constraint(
        op.f("fk_notifications_installment_id_installments"),
        "notifications",
        type_="foreignkey",
    )
    op.drop_column("notifications", "installment_id")
