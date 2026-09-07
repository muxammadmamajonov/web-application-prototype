"""Add short, stable referral code to users (R56)

Replaces the old UUID-based referral deep link (?startapp=ref_<uuid>, 36 chars)
with a short ?startapp=ref_<code> (8 chars). Backfills every existing user with
a unique code so legacy long links are superseded.

Revision ID: r56userrefcode
Revises: r55categoryschemas
Create Date: 2026-06-01 15:00:00+00:00
"""
from __future__ import annotations

import secrets

import sqlalchemy as sa
from alembic import op

revision = "r56userrefcode"
down_revision = "r55categoryschemas"
branch_labels = None
depends_on = None

_ALPHABET = "abcdefghijkmnpqrstuvwxyzACDEFGHJKLMNPQRSTUVWXYZ23456789"
_LEN = 8


def _code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_LEN))


def upgrade() -> None:
    op.add_column("users", sa.Column("ref_code", sa.String(length=16), nullable=True))

    conn = op.get_bind()
    ids = [r[0] for r in conn.execute(sa.text("SELECT id FROM users")).fetchall()]
    used: set[str] = set()
    for uid in ids:
        code = _code()
        while code in used:
            code = _code()
        used.add(code)
        conn.execute(
            sa.text("UPDATE users SET ref_code = :c WHERE id = :id"),
            {"c": code, "id": uid},
        )

    op.create_index("ix_users_ref_code", "users", ["ref_code"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_ref_code", table_name="users")
    op.drop_column("users", "ref_code")
