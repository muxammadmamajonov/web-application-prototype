"""#99 — subscription invoices carry a gapless sequential number.

DB-free: the invoice_number column is unique; the SequenceCounter table exists;
generate_invoice assigns the number; and the counter uses a rollback-safe
upsert-increment (not a gap-prone DB sequence). The actual no-gaps behaviour is
exercised by a real-DB increment in CI; VAT/QQS wording is a [LEGAL_REVIEW] item.
"""
from __future__ import annotations

import inspect
from pathlib import Path


def test_invoice_has_unique_number_column() -> None:
    from app.models import Invoice

    col = Invoice.__table__.c.invoice_number
    assert col.unique is True


def test_sequence_counter_table() -> None:
    from app.models import SequenceCounter

    assert SequenceCounter.__tablename__ == "sequence_counters"
    cols = SequenceCounter.__table__.c
    assert "name" in cols and cols["name"].primary_key
    assert "value" in cols


def test_generate_invoice_assigns_a_number() -> None:
    from app.services.billing import generate_invoice

    assert "next_invoice_number" in inspect.getsource(generate_invoice)


def test_numbering_is_gapless_upsert_not_sequence() -> None:
    from app.services.billing import next_invoice_number

    src = inspect.getsource(next_invoice_number)
    migration = (
        Path(__file__).parents[1]
        / "alembic/versions/r99_global_rls_least_privilege.py"
    ).read_text()
    # An atomic upsert-increment rolls back with the txn (no gaps); a DB SEQUENCE
    # would not. The mutation now lives in a narrow SECURITY DEFINER function so
    # tenant requests cannot inspect the global counter table directly.
    assert "next_invoice_sequence" in src
    assert "ON CONFLICT" in migration
    assert "SECURITY DEFINER" in migration
    assert "nextval" not in src + migration
