"""MUDD-121 — a report export must not drop a non-primary currency's money.

(MUDD-114) `services/reports.py` computes every row's legacy scalar
(`volume`, `commission`, `revenue`, `outstanding`, `amount`, `lifetime_value`,
`paid`, `cost_known`, `gross_profit`, `margin_pct`) as the PRIMARY currency's
share, alongside a `*_by_currency` map holding the full picture. The JSON
response already carries both. The file export — CSV / XLSX / PDF / Telegram,
all built from the same `columns` + `rows` pair — read ONLY the legacy
scalars, so a seller whose entire volume was in a non-primary currency showed
`sales_count > 0` next to `volume = 0` and `commission = 0`: real activity,
invisible payout.

DB-free, mirroring `test_round24.py`'s style: these exercise the column /
row-shaping helpers directly against realistic service-output shapes (the
same field names `services/reports.py` actually returns), not the DB-backed
endpoint.
"""
from __future__ import annotations

from app.api import reports

# ── the primitive ────────────────────────────────────────────────────────────

def test_explode_by_currency_splits_one_row_per_currency_present() -> None:
    rows = [{
        "name": "Anvar", "sales_count": 3,
        "volume_by_currency": {"UZS": 45_000_000.0, "USD": 3000.0},
        "commission_by_currency": {"UZS": 2_250_000.0, "USD": 150.0},
    }]
    out = reports._explode_by_currency(
        rows, {"volume": "volume_by_currency", "commission": "commission_by_currency"})
    assert len(out) == 2
    by_currency = {r["currency"]: r for r in out}
    assert by_currency["UZS"]["volume"] == 45_000_000.0
    assert by_currency["UZS"]["commission"] == 2_250_000.0
    assert by_currency["USD"]["volume"] == 3000.0
    assert by_currency["USD"]["commission"] == 150.0
    # Non-money fields repeat on every currency row for the same entity —
    # they are not the figure that was going missing.
    assert by_currency["UZS"]["name"] == by_currency["USD"]["name"] == "Anvar"
    assert by_currency["UZS"]["sales_count"] == by_currency["USD"]["sales_count"] == 3


def test_explode_by_currency_keeps_a_currency_less_row_once() -> None:
    # A bucket / entity with genuinely nothing in it (e.g. an empty aging
    # bucket) must not vanish from the export — it becomes a single row with
    # no currency, not zero rows.
    rows = [{"bucket": "90+", "count": 0, "outstanding_by_currency": {}}]
    out = reports._explode_by_currency(rows, {"outstanding": "outstanding_by_currency"})
    assert len(out) == 1
    assert out[0]["bucket"] == "90+"
    assert "currency" not in out[0]


def test_currency_less_row_still_prints_its_number() -> None:
    # (MUDD-121, review) An empty aging bucket has an empty by-currency map, so
    # it takes the keep-once path above — and then the export prints whatever
    # the row already carries. The endpoint therefore has to leave the legacy
    # scalar ON the row: a blank "Qarz" cell where 0 belongs is the same
    # silent-blank shape this lane exists to close, not a tidier zero.
    rows = reports._explode_by_currency(
        [{"bucket": "90+", "count": 0, "outstanding": 0, "outstanding_by_currency": {}}],
        {"outstanding": "outstanding_by_currency"})
    csv_text = reports._csv_bytes(
        [("bucket", "Kun"), ("count", "Soni"), ("currency", "Valyuta"),
         ("outstanding", "Qarz")], rows).decode("utf-8-sig")
    assert csv_text.splitlines()[1] == "90+,0,,0"


def test_explode_by_currency_unions_fields_with_different_currency_sets() -> None:
    # `margin`'s `margin_pct_by_currency` only covers currencies with nonzero
    # revenue, a strict subset of `revenue_by_currency` — the row for a
    # currency present in one map but not another must still appear, with the
    # missing figure defaulted rather than the whole row dropped.
    row = {
        "revenue_by_currency": {"UZS": 100.0, "USD": 50.0},
        "margin_pct_by_currency": {"UZS": 20.0},
    }
    out = reports._explode_by_currency(
        [row], {"revenue": "revenue_by_currency", "margin_pct": "margin_pct_by_currency"})
    by_currency = {r["currency"]: r for r in out}
    assert by_currency["USD"]["revenue"] == 50.0
    assert by_currency["USD"]["margin_pct"] == 0


# ── the regression: staff-performance payroll export ────────────────────────

# A tenant with one UZS-only seller and one USD-only seller — the shape that
# made the previous HIGH: a seller's ENTIRE volume in a non-primary currency
# narrows their legacy `volume`/`commission` scalars to zero, exactly as if
# they had sold nothing.
_MIXED_STAFF_ROWS = [
    {
        "user_id": "u1", "name": "Anvar", "sales_count": 2,
        "volume": 45_000_000.0, "commission_rate_pct": 5.0, "commission": 2_250_000.0,
        "volume_by_currency": {"UZS": 45_000_000.0},
        "commission_by_currency": {"UZS": 2_250_000.0},
    },
    {
        "user_id": "u2", "name": "Malika", "sales_count": 1,
        # Narrowed-to-primary scalars: this is what the service layer hands
        # the export when the seller's only sale is in USD (not the primary).
        "volume": 0.0, "commission_rate_pct": 5.0, "commission": 0.0,
        "volume_by_currency": {"USD": 3000.0},
        "commission_by_currency": {"USD": 150.0},
    },
]

# The column list `report_staff` used before this fix — reading only the
# legacy scalars, no `currency` column.
_OLD_STAFF_COLUMNS = [
    ("name", "Sotuvchi"), ("sales_count", "Sotuvlar"),
    ("volume", "Hajm"), ("commission_rate_pct", "Stavka %"),
    ("commission", "Komissiya"),
]

_NEW_STAFF_COLUMNS = [
    ("name", "Sotuvchi"), ("sales_count", "Sotuvlar"), ("currency", "Valyuta"),
    ("volume", "Hajm"), ("commission_rate_pct", "Stavka %"), ("commission", "Komissiya"),
]


def test_red_the_old_column_list_lost_the_usd_only_sellers_commission() -> None:
    """Documents the HIGH: exporting the raw rows through the pre-fix column
    list shows Malika with real sales and a payout of zero."""
    csv_text = reports._csv_bytes(_OLD_STAFF_COLUMNS, _MIXED_STAFF_ROWS).decode("utf-8-sig")
    lines = csv_text.strip().splitlines()
    malika = next(line for line in lines if line.startswith("Malika"))
    assert malika == "Malika,1,0.0,5.0,0.0"
    assert "150" not in csv_text, "the old shape must show 150 nowhere — that's the bug"


def test_green_the_fixed_export_shows_the_usd_commission() -> None:
    """The fix: explode by currency before handing rows to the CSV writer —
    the same transform `report_staff` now applies before `_respond`."""
    rows = reports._explode_by_currency(
        _MIXED_STAFF_ROWS,
        {"volume": "volume_by_currency", "commission": "commission_by_currency"})
    csv_text = reports._csv_bytes(_NEW_STAFF_COLUMNS, rows).decode("utf-8-sig")
    lines = csv_text.strip().splitlines()
    assert lines[0] == "Sotuvchi,Sotuvlar,Valyuta,Hajm,Stavka %,Komissiya"
    malika = next(line for line in lines if line.startswith("Malika"))
    assert malika == "Malika,1,USD,3000.0,5.0,150.0"
    anvar = next(line for line in lines if line.startswith("Anvar"))
    assert anvar == "Anvar,2,UZS,45000000.0,5.0,2250000.0"


# ── the other five reports keep their `*_by_currency` money too ─────────────

def test_aging_export_keeps_every_currencys_outstanding() -> None:
    bucket = {"bucket": "0–30", "count": 4,
              "outstanding_by_currency": {"UZS": 1_000_000.0, "USD": 200.0}}
    out = reports._explode_by_currency([bucket], {"outstanding": "outstanding_by_currency"})
    assert {r["currency"]: r["outstanding"] for r in out} == {"UZS": 1_000_000.0, "USD": 200.0}
    # `count` is bucket-wide, not per-currency — untouched, not the figure at risk.
    assert all(r["count"] == 4 for r in out)


def test_cashflow_export_keeps_every_currencys_amount() -> None:
    period = {"period": "2026-09-01", "count": 5,
              "by_currency": {"UZS": 500_000.0, "USD": 40.0}}
    out = reports._explode_by_currency([period], {"amount": "by_currency"})
    assert {r["currency"]: r["amount"] for r in out} == {"UZS": 500_000.0, "USD": 40.0}


def test_products_export_keeps_every_currencys_revenue() -> None:
    row = {"brand": "Samsung", "category": "smartfonlar", "qty": 7,
           "revenue_by_currency": {"UZS": 70_000_000.0, "USD": 1200.0}}
    out = reports._explode_by_currency([row], {"revenue": "revenue_by_currency"})
    assert {r["currency"]: r["revenue"] for r in out} == {"UZS": 70_000_000.0, "USD": 1200.0}


def test_ltv_export_keeps_every_currencys_lifetime_value_and_paid() -> None:
    row = {
        "customer_id": "c1", "name": "Dilnoza", "phone": "+998901112233", "contracts": 3,
        "lifetime_value_by_currency": {"UZS": 30_000_000.0, "USD": 500.0},
        "paid_by_currency": {"UZS": 10_000_000.0, "USD": 500.0},
    }
    out = reports._explode_by_currency(
        [row], {"lifetime_value": "lifetime_value_by_currency", "paid": "paid_by_currency"})
    by_currency = {r["currency"]: r for r in out}
    assert by_currency["USD"]["lifetime_value"] == 500.0
    assert by_currency["USD"]["paid"] == 500.0
    assert by_currency["UZS"]["lifetime_value"] == 30_000_000.0


def test_margin_export_keeps_every_currencys_figures() -> None:
    data = {
        "currency": "UZS", "revenue": 100_000_000.0, "cost_known": 60_000_000.0,
        "gross_profit": 40_000_000.0, "margin_pct": 40.0,
        "qty_with_cost": 12, "qty_total": 15,
        "revenue_by_currency": {"UZS": 100_000_000.0, "USD": 2000.0},
        "cost_known_by_currency": {"UZS": 60_000_000.0, "USD": 1200.0},
        "gross_profit_by_currency": {"UZS": 40_000_000.0, "USD": 800.0},
        "margin_pct_by_currency": {"UZS": 40.0, "USD": 40.0},
    }
    out = reports._explode_by_currency([data], {
        "revenue": "revenue_by_currency", "cost_known": "cost_known_by_currency",
        "gross_profit": "gross_profit_by_currency", "margin_pct": "margin_pct_by_currency",
    })
    by_currency = {r["currency"]: r for r in out}
    assert by_currency["USD"]["revenue"] == 2000.0
    assert by_currency["USD"]["gross_profit"] == 800.0
    # Non-per-currency figures repeat on every row rather than being dropped.
    assert by_currency["USD"]["qty_total"] == 15
