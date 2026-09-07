"""Invitation credentials stored as verifiers, and provenance enforced in SQL.

Revision ID: rd0invverifier
Revises: rc0verifiedid
Create Date: 2026-08-10

Two things a database dump should not give away, and one the application should
not be the only guard for.

INVITATIONS. An invitation is a bearer credential: whoever holds the link can
bind a Telegram account to a shop's customer record. It was stored in plaintext,
so a dump handed over every live one. New rows keep an HMAC verifier instead.
Legacy plaintext rows are left exactly as they are and keep resolving until
their own `expires_at` passes — the population can only shrink, because nothing
mints plaintext any more.

PROVENANCE. `rc0verifiedid` added a CHECK covering the phone and its source.
The eligibility code was left to the application, which means a direct UPDATE
could leave a verified number with no idea whether it may trade. All four
columns now stand or fall together.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "rd0invverifier"
down_revision = "rc0verifiedid"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    live = conn.execute(sa.text(
        "SELECT count(*) FROM invitations WHERE used_at IS NULL AND expires_at > now()"
    )).scalar_one()
    total = conn.execute(sa.text("SELECT count(*) FROM invitations")).scalar_one()
    print(f"[rd0invverifier] invitations={total}, of which live plaintext={live} "
          "— kept until their own expiry; no new plaintext is minted")

    op.add_column("invitations", sa.Column("token_verifier", sa.String(64), nullable=True))
    op.create_index(
        "uq_invitations_token_verifier", "invitations", ["token_verifier"], unique=True,
    )
    # Legacy rows are the only ones that may carry plaintext, so the column can
    # no longer be required.
    op.alter_column("invitations", "token", existing_type=sa.String(40), nullable=True)
    # Exactly one identifying secret per row: a legacy plaintext or a verifier,
    # never both and never neither.
    op.create_check_constraint(
        "ck_invitations_one_credential", "invitations",
        "(token IS NOT NULL) <> (token_verifier IS NOT NULL)",
    )

    # Provenance: all four, or none of the four.
    op.drop_constraint("ck_users_verified_phone_provenance", "users", type_="check")
    op.create_check_constraint(
        "ck_users_verified_phone_provenance", "users",
        "(verified_phone IS NULL AND verified_phone_source IS NULL "
        " AND verified_phone_at IS NULL AND phone_eligibility IS NULL) "
        "OR (verified_phone IS NOT NULL AND verified_phone_source IS NOT NULL "
        "    AND verified_phone_at IS NOT NULL AND phone_eligibility IS NOT NULL)",
    )
    # Closed vocabularies, in SQL rather than only in a Python enum.
    op.create_check_constraint(
        "ck_users_verified_phone_source", "users",
        "verified_phone_source IS NULL "
        "OR verified_phone_source IN ('telegram_contact', 'telegram_oidc')",
    )
    op.create_check_constraint(
        "ck_users_phone_eligibility", "users",
        "phone_eligibility IS NULL "
        "OR phone_eligibility IN ('ELIGIBLE', 'UNSUPPORTED_REGION', 'MALFORMED')",
    )
    # Canonical E.164, enforced where it is stored. A value that reached the
    # column by any other route than the verification service is still refused.
    op.create_check_constraint(
        "ck_users_verified_phone_e164", "users",
        r"verified_phone IS NULL OR verified_phone ~ '^\+[1-9][0-9]{7,14}$'",
    )
    print("[rd0invverifier] verifier column added; provenance and E.164 enforced in SQL")


def downgrade() -> None:
    conn = op.get_bind()

    # A verifier cannot be turned back into a credential — that is the whole
    # point of storing it. Dropping the column would leave those rows with no
    # usable secret at all: the links in people's chats would stop resolving
    # and nothing could reissue them, because nothing can read them.
    #
    # An empty database, or one holding only legacy plaintext, downgrades
    # cleanly. Anything else stops here.
    orphaned = conn.execute(sa.text(
        "SELECT count(*) FROM invitations WHERE token_verifier IS NOT NULL"
    )).scalar_one()
    if orphaned:
        live = conn.execute(sa.text(
            "SELECT count(*) FROM invitations "
            "WHERE token_verifier IS NOT NULL AND used_at IS NULL AND expires_at > now()"
        )).scalar_one()
        raise RuntimeError(
            f"[rd0invverifier] {orphaned} invitation(s) ({live} still live) exist only as a "
            "verifier. Their credentials cannot be reconstructed, so dropping the column "
            "would destroy them. Revoke them, let them expire, or apply a reviewed forward "
            "repair — then downgrade. Nothing has been changed."
        )

    for name in ("ck_users_verified_phone_e164", "ck_users_phone_eligibility",
                 "ck_users_verified_phone_source"):
        op.drop_constraint(name, "users", type_="check")
    op.drop_constraint("ck_users_verified_phone_provenance", "users", type_="check")
    op.create_check_constraint(
        "ck_users_verified_phone_provenance", "users",
        "(verified_phone IS NULL) = (verified_phone_source IS NULL) AND "
        "(verified_phone IS NULL OR verified_phone_at IS NOT NULL)",
    )
    op.drop_constraint("ck_invitations_one_credential", "invitations", type_="check")
    # Rows minted as verifier-only have no plaintext to restore; a NOT NULL
    # column would be a lie about them, so the nullability stays.
    op.drop_index("uq_invitations_token_verifier", table_name="invitations")
    op.drop_column("invitations", "token_verifier")
