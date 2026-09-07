#!/usr/bin/env python
"""Reconcile `users.phone` against `users.verified_phone`. Read-only. No PII.

WHY THIS IS A REPORT AND NOT A MIGRATION.

The normalisation makes `verified_phone` the account's one canonical number and
stops every path that wrote the typed `phone`. The obvious next step — backfill
`verified_phone` from `phone` where it is missing — is the one thing that must
never happen: `verified_phone` means "somebody demonstrated they can answer this
number", and copying a typed value into it would manufacture exactly the proof
the column exists to require. It would also collide with
`uq_users_verified_phone_digits` the moment two people had typed the same number.

So the policy is:

  both present, equal      nothing to do — the typed row is a harmless duplicate
  verified only            already canonical
  typed only               NOT promoted. The account has no canonical phone until
                           its owner shares their Telegram contact, which the
                           product now asks for at the one moment it matters.
                           Counted here so the number of such accounts is known.
  both present, conflict   NOT resolved. Two different numbers for one person is
                           a fact about that person that no rule can settle from
                           the outside. Listed by user id for manual review.
  malformed                listed by user id.

Run against production:

    fly ssh console -a muddato-api -C "python /app/scripts/phone_identity_report.py"

Prints COUNTS, and user IDs for the rows needing a human. It never prints a phone
number: this is meant to be safe to paste into an issue.
"""
from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Digits-only, matching what `uq_users_phone_digits` and `phone_identity` mean by
# "the same number" — comparing the raw strings would report "+998 90 …" and
# "99890…" as a conflict.
_D = r"regexp_replace({col}, '\D', '', 'g')"

SUMMARY = f"""
SELECT
  count(*)                                                             AS users_total,
  count(*) FILTER (WHERE verified_phone IS NOT NULL)                   AS canonical_present,
  count(*) FILTER (WHERE phone IS NOT NULL)                            AS legacy_present,
  count(*) FILTER (WHERE phone IS NOT NULL AND verified_phone IS NOT NULL
                   AND {_D.format(col='phone')} = {_D.format(col='verified_phone')})
                                                                       AS agree,
  count(*) FILTER (WHERE phone IS NOT NULL AND verified_phone IS NOT NULL
                   AND {_D.format(col='phone')} <> {_D.format(col='verified_phone')})
                                                                       AS conflict,
  count(*) FILTER (WHERE phone IS NOT NULL AND verified_phone IS NULL) AS legacy_only,
  count(*) FILTER (WHERE phone IS NULL AND verified_phone IS NULL)     AS no_phone_at_all,
  count(*) FILTER (WHERE verified_phone IS NOT NULL
                   AND phone_eligibility IS DISTINCT FROM 'ELIGIBLE')  AS proved_but_not_eligible,
  count(*) FILTER (WHERE verified_phone IS NOT NULL
                   AND verified_phone_source IS NULL)                  AS proved_without_source
FROM users
"""

CONFLICTS = f"""
SELECT id::text FROM users
WHERE phone IS NOT NULL AND verified_phone IS NOT NULL
  AND {_D.format(col='phone')} <> {_D.format(col='verified_phone')}
ORDER BY created_at
"""

MALFORMED = f"""
SELECT id::text FROM users
WHERE phone IS NOT NULL
  AND length({_D.format(col='phone')}) NOT BETWEEN 8 AND 15
ORDER BY created_at
"""

LEGACY_ONLY = """
SELECT id::text FROM users
WHERE phone IS NOT NULL AND verified_phone IS NULL
ORDER BY created_at
"""


async def main() -> int:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    # Production sits behind pgbouncer in transaction mode, which does not carry
    # prepared statements across pooled connections — the app's own engine
    # already disables the caches for exactly this reason (`app/db.py`), and an
    # ops script that forgets to dies on `DuplicatePreparedStatementError`.
    engine = create_async_engine(
        url,
        connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0},
    )
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(text(SUMMARY))).mappings().one()
            print("== phone identity reconciliation ==")
            for k, v in row.items():
                print(f"  {k:26} {v}")

            for label, sql, verdict in (
                ("CONFLICT (manual review required)", CONFLICTS,
                 "two different numbers for one person — decide with the person, not in code"),
                ("MALFORMED legacy value", MALFORMED,
                 "not a phone number; safe to clear once confirmed"),
                ("LEGACY ONLY (no proof yet)", LEGACY_ONLY,
                 "no action: they gain a canonical number by sharing their Telegram contact"),
            ):
                ids = [r[0] for r in (await conn.execute(text(sql))).all()]
                print(f"\n-- {label}: {len(ids)}")
                print(f"   {verdict}")
                for i in ids:
                    print(f"   user_id={i}")
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
