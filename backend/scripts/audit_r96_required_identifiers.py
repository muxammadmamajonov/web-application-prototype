"""READ-ONLY: how many existing contract lines can no longer be edited (MUDD-123).

`r96_seed_identifier_validators.py` (`r96catidschema`) gave all 114 seeded
catalog categories a **required** identifier field plus a `validator`, applied
retroactively and with no backfill. `PUT /installments/{iid}` deletes and
recreates every line, so it re-runs the *current* rules over *old* data: a
contract whose line never captured the value — or captured one that the new
validator now rejects — cannot be saved at all, even to fix an unrelated field.

This script measures that blast radius. It writes nothing: one SELECT per
table, `default_transaction_read_only` on, no DDL, no UPDATE.

It reuses the API's own validators (`app.core.category_identifiers` +
`app.core.catalog_identifiers`) rather than reimplementing them in SQL, so the
count cannot drift from what the endpoint actually enforces.

Usage::

    uv run python -m scripts.audit_r96_required_identifiers \\
        --url postgresql+psycopg://user:pass@host:5432/db

The URL may also come from ``AUDIT_DATABASE_URL`` or ``DATABASE_URL_SYNC``.
Add ``--json`` for machine-readable output.

Reasons reported per failing (item, key):

* ``missing``  — the field is required now and no value was ever recorded.
* ``unknown``  — a stored key the category's schema no longer declares.
* everything else is the validator's own code, e.g.
  ``catalog_identifier_imei_checksum`` (stored IMEI fails Luhn),
  ``catalog_identifier_vin_charset`` (stored VIN contains I/O/Q).

The **contracts** figure is the one that matters operationally: `PUT` is only
reachable while a contract has no payments and is neither completed nor
cancelled, so a line on a fully-paid contract is dead weight, not a blocked
edit.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

import sqlalchemy as sa

from app.core.catalog_identifiers import find_identifier_field, validate_catalog_identifier
from app.core.category_identifiers import parse_identifier_schema

#: `replace_installment` refuses these before it ever reaches the identifier
#: check, so a line under one of them is not an edit anybody can be blocked on.
_EDITABLE_STATUSES = ("active", "pending", "overdue", "draft")


def _resolve_url(cli_url: str | None) -> str:
    url = cli_url or os.environ.get("AUDIT_DATABASE_URL") or os.environ.get("DATABASE_URL_SYNC")
    if not url:
        sys.exit(
            "no database URL — pass --url, or set AUDIT_DATABASE_URL / DATABASE_URL_SYNC",
        )
    # asyncpg cannot drive a plain `create_engine`; the audit is synchronous.
    return url.replace("+asyncpg", "+psycopg")


def collect(conn: sa.Connection) -> dict:
    schemas = {
        slug: raw
        for slug, raw in conn.execute(
            sa.text("SELECT slug, identifier_schema FROM catalog_categories"),
        ).all()
    }

    items = conn.execute(sa.text(
        """
        SELECT i.id            AS item_id,
               i.category      AS category,
               ins.id          AS installment_id,
               ins.status::text AS status,
               COALESCE(ins.paid_count, 0)  AS paid_count,
               COALESCE(ins.paid_amount, 0) AS paid_amount
          FROM installment_items i
          JOIN installments ins ON ins.id = i.installment_id
        """,
    )).mappings().all()

    stored: dict[object, dict[str, str]] = defaultdict(dict)
    for item_id, key, value in conn.execute(sa.text(
        "SELECT item_id, key, value FROM installment_item_identifiers",
    )).all():
        stored[item_id][key] = value

    by_category: dict[str, Counter] = defaultdict(Counter)
    reasons: Counter = Counter()
    failing_items = 0
    blocked_contracts: set[object] = set()
    blocked_editable_contracts: set[object] = set()
    scanned_items = 0
    editable_items = 0

    for row in items:
        slug = row["category"]
        if not slug or slug not in schemas:
            continue
        raw_schema = schemas[slug]
        raw_schema = raw_schema if isinstance(raw_schema, list) else []
        fields = parse_identifier_schema(raw_schema)
        if not fields:
            continue
        scanned_items += 1
        editable = (
            row["status"] in _EDITABLE_STATUSES
            and row["paid_count"] == 0
            and row["paid_amount"] == 0
        )
        if editable:
            editable_items += 1

        values = stored.get(row["item_id"], {})
        item_failed = False

        unknown_keys = set(values) - {f.key for f in fields}
        if unknown_keys:
            item_failed = True
            reasons["unknown"] += len(unknown_keys)
            by_category[slug]["unknown"] += len(unknown_keys)

        for field in fields:
            value = values.get(field.key)
            if value is None:
                if field.required:
                    item_failed = True
                    reasons["missing"] += 1
                    by_category[slug]["missing"] += 1
                continue
            definition = find_identifier_field(raw_schema, field.key)
            if definition is None:
                continue
            _checked, err = validate_catalog_identifier(definition, value)
            if err:
                item_failed = True
                reasons[err] += 1
                by_category[slug][err] += 1

        if item_failed:
            failing_items += 1
            blocked_contracts.add(row["installment_id"])
            if editable:
                blocked_editable_contracts.add(row["installment_id"])

    return {
        "categories_with_a_schema": sum(
            1 for raw in schemas.values() if parse_identifier_schema(raw)
        ),
        "categories_with_a_required_field": sum(
            1 for raw in schemas.values()
            if any(f.required for f in parse_identifier_schema(raw))
        ),
        "items_total": len(items),
        "items_scanned": scanned_items,
        "items_scanned_on_editable_contracts": editable_items,
        "items_failing": failing_items,
        "contracts_with_a_failing_line": len(blocked_contracts),
        "contracts_blocked_from_editing": len(blocked_editable_contracts),
        "reasons": dict(sorted(reasons.items())),
        "by_category": {
            slug: dict(sorted(counter.items()))
            for slug, counter in sorted(by_category.items())
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    engine = sa.create_engine(_resolve_url(args.url))
    with engine.connect() as conn:
        # Belt and braces: even a typo'd query cannot write from here.
        conn.execute(sa.text("SET TRANSACTION READ ONLY"))
        report = collect(conn)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    print(f"categories with an identifier_schema : {report['categories_with_a_schema']}")
    print(f"  ...of which have a required field  : {report['categories_with_a_required_field']}")
    print(f"installment_items total              : {report['items_total']}")
    print(f"  ...under a schema-bearing category : {report['items_scanned']}")
    print(f"  ...of those, on an editable contract: {report['items_scanned_on_editable_contracts']}")
    print(f"installment_items that would FAIL     : {report['items_failing']}")
    print(f"contracts with >=1 failing line       : {report['contracts_with_a_failing_line']}")
    print(f"contracts BLOCKED from being edited   : {report['contracts_blocked_from_editing']}")
    print("\nby reason:")
    for reason, count in report["reasons"].items():
        print(f"  {reason:<40} {count}")
    print("\nby category (failing field-checks):")
    for slug, counter in report["by_category"].items():
        detail = ", ".join(f"{r}={c}" for r, c in counter.items())
        print(f"  {slug:<32} {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
