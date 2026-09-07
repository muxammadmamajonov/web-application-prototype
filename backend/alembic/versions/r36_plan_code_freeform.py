"""Plan codes free-form: plan_code enum → varchar (Round 36)

Lets the platform owner create up to 5 arbitrary plans instead of being
boxed into the four fixed enum tiers. Converts every column that used the
native ``plan_code`` enum to ``varchar(32)`` and drops the enum type.

Revision ID: r36plancodefreeform
Revises: r35businesstype
Create Date: 2026-05-30 16:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "r36plancodefreeform"
down_revision = "r35businesstype"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Every column sharing the native ``plan_code`` enum must be converted
    # before the type can be dropped.
    op.alter_column(
        "plans", "code",
        type_=sa.String(length=32), existing_nullable=False,
        postgresql_using="code::text",
    )
    op.alter_column(
        "tenants", "plan_code",
        type_=sa.String(length=32), existing_nullable=False,
        postgresql_using="plan_code::text",
    )
    op.alter_column(
        "invoices", "plan_code",
        type_=sa.String(length=32), existing_nullable=False,
        postgresql_using="plan_code::text",
    )
    op.execute("ALTER TABLE tenants ALTER COLUMN plan_code SET DEFAULT 'trial'")
    op.execute("DROP TYPE IF EXISTS plan_code")


def downgrade() -> None:
    op.execute("ALTER TABLE tenants ALTER COLUMN plan_code DROP DEFAULT")
    op.execute(
        "CREATE TYPE plan_code AS ENUM ('trial', 'lite', 'pro', 'custom')"
    )
    # Rows with non-enum codes would break the cast; coerce them to 'custom'.
    op.execute(
        "UPDATE plans SET code = 'custom' "
        "WHERE code NOT IN ('trial', 'lite', 'pro', 'custom')"
    )
    op.execute(
        "UPDATE tenants SET plan_code = 'custom' "
        "WHERE plan_code NOT IN ('trial', 'lite', 'pro', 'custom')"
    )
    op.execute(
        "UPDATE invoices SET plan_code = 'custom' "
        "WHERE plan_code NOT IN ('trial', 'lite', 'pro', 'custom')"
    )
    op.alter_column(
        "plans", "code",
        type_=sa.Enum(name="plan_code"), existing_nullable=False,
        postgresql_using="code::plan_code",
    )
    op.alter_column(
        "tenants", "plan_code",
        type_=sa.Enum(name="plan_code"), existing_nullable=False,
        postgresql_using="plan_code::plan_code",
    )
    op.alter_column(
        "invoices", "plan_code",
        type_=sa.Enum(name="plan_code"), existing_nullable=False,
        postgresql_using="plan_code::plan_code",
    )
