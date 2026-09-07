"""Migration `ra0currency` must refuse to guess a contract's currency.

The backfill copies each tenant's CURRENT configured currency onto its existing
contracts. That is only correct where the current setting is also the one those
contracts were written under. Two cases break it, and both used to be printed to
the deployment log and then backfilled anyway — which is a warning nobody reads
until a customer holds a receipt in the wrong unit:

  * a tenant that changed currency AFTER writing contracts;
  * a configured currency outside the supported set, previously coerced to UZS.

Both now abort before a single row is written. Alembic is configured
`transaction_per_migration` and Postgres has transactional DDL, so an abort
leaves the database at r99globalrls with no columns, no data and no version bump
— asserted below rather than assumed.

These tests drive the REAL migration through alembic against the real schema.
Asserting on a hand-rolled copy of the SQL would prove nothing about what runs
during a deploy.
"""
from __future__ import annotations

import os
import re

import pytest

PREVIOUS = "r99globalrls"
TARGET = "ra0currency"

pytestmark = pytest.mark.usefixtures("at_previous_revision")


def _sync_url() -> str | None:
    """The migration runs through alembic, which reads the SYNC url."""
    return os.getenv("TEST_DATABASE_URL")


@pytest.fixture
def db():
    """A sync engine on the integration-tier database."""
    if not _sync_url():
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")
    from sqlalchemy import create_engine

    from app.config import settings

    # The MIGRATION credential, not the runtime one. This fixture drops and
    # recreates schema ``public`` and runs Alembic; ``database_url_sync`` is the
    # restricted application login, which by design can do neither.
    engine = create_engine(settings.migration_url_sync, future=True)
    try:
        yield engine
    finally:
        engine.dispose()


def _alembic(target: str) -> None:
    from alembic.config import Config

    from alembic import command

    command.upgrade(Config("alembic.ini"), target)


def _alembic_down(target: str) -> None:
    from alembic.config import Config

    from alembic import command

    command.downgrade(Config("alembic.ini"), target)


@pytest.fixture
def at_previous_revision(db):
    """A clean schema migrated to exactly the revision before this one.

    Left clean afterwards so a following fixture (the model-only `isolation_ctx`)
    is not tripped by migration-built constraints its drop_all cannot order.
    """
    from sqlalchemy import text

    with db.begin() as c:
        c.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    _alembic(PREVIOUS)
    try:
        yield
    finally:
        with db.begin() as c:
            c.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))


# ── seeding, in the shape the schema has at r99globalrls ────────────────────

def _seed(db, *, currency: str | None, contracts: int = 2,
          tenant: str = "11111111-1111-1111-1111-111111111111",
          slug: str = "shop", with_settings: bool = True) -> None:
    from sqlalchemy import text

    with db.begin() as c:
        c.execute(text(
            "INSERT INTO tenants (id,slug,name,status,locale,timezone,onboarding_state,"
            "balance,created_at,updated_at) VALUES "
            "(:id,:slug,:slug,'active','uz','Asia/Tashkent','{}',0,now(),now())"
        ), {"id": tenant, "slug": slug})
        if with_settings:
            c.execute(text(
                "INSERT INTO tenant_settings (id,tenant_id,business_hours,currency,"
                "default_locale,notification_prefs,reminder_offsets_days,extra,"
                "product_form_fields,created_at,updated_at) VALUES "
                "(gen_random_uuid(),:t,'{}',:cur,'uz','{}','[]','{}','{}',now(),now())"
            ), {"t": tenant, "cur": currency})
        c.execute(text(
            "INSERT INTO customers (id,tenant_id,full_name,phone,passport_photo_urls,"
            "status,metadata,created_at,updated_at) VALUES "
            "(gen_random_uuid(),:t,'C',:phone,'[]','active','{}',now(),now())"
        ), {"t": tenant, "phone": f"+9989000{slug[:5]}"})
        for _ in range(contracts):
            c.execute(text(
                "INSERT INTO installments (id,tenant_id,customer_id,status,total_amount,"
                "down_payment,principal,months,monthly_amount,paid_count,paid_amount,"
                "start_date,calc_formula,created_at,updated_at) "
                "SELECT gen_random_uuid(),:t,cu.id,'active',6000000,0,6000000,6,1000000,0,0,"
                "current_date,'none',now(),now() FROM customers cu WHERE cu.tenant_id=:t LIMIT 1"
            ), {"t": tenant})
        c.execute(text(
            "INSERT INTO payments (id,tenant_id,installment_id,customer_id,amount,method,"
            "status,created_at,updated_at) "
            "SELECT gen_random_uuid(), i.tenant_id, i.id, i.customer_id, 1000000,'cash',"
            "'approved',now(),now() FROM installments i WHERE i.tenant_id=:t"
        ), {"t": tenant})


def _audit_currency_change(db, *, tenant: str, when: str, before: str = "UZS",
                           after: str = "USD") -> None:
    """An audited settings change at `when` (an SQL interval expression)."""
    from sqlalchemy import text

    with db.begin() as c:
        c.execute(text(
            "INSERT INTO audit_log (id,tenant_id,action,entity_type,entity_id,before,after,"
            f"created_at,updated_at) VALUES (gen_random_uuid(),:t,'settings.update','settings',"
            # CAST(...) rather than `::jsonb`: SQLAlchemy reads `:b::jsonb` as a
            # parameter named `b:` and the SQL never reaches Postgres intact.
            f"'s1', CAST(:b AS jsonb), CAST(:a AS jsonb), {when}, {when})"
        ), {"t": tenant, "b": f'{{"currency":"{before}"}}',
            "a": f'{{"currency":"{after}"}}'})


def _revision(db) -> str | None:
    from sqlalchemy import text

    with db.connect() as c:
        return c.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _has_currency_columns(db) -> bool:
    from sqlalchemy import text

    with db.connect() as c:
        return bool(c.execute(text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name IN ('installments','payments') AND column_name='currency'"
        )).scalar())


def _distribution(db, table: str) -> dict[str, int]:
    from sqlalchemy import text

    with db.connect() as c:
        return {row[0]: row[1] for row in c.execute(text(
            f"SELECT currency, count(*) FROM {table} GROUP BY 1"   # table is a fixed literal
        ))}


# ── it succeeds where the backfill is provably correct ──────────────────────

def test_zero_historical_conflicts_migrates(db) -> None:
    _seed(db, currency="UZS", contracts=3)
    _alembic(TARGET)
    assert _revision(db) == TARGET
    assert _distribution(db, "installments") == {"UZS": 3}
    assert _distribution(db, "payments") == {"UZS": 3}


def test_both_supported_currencies_backfill_correctly(db) -> None:
    _seed(db, currency="UZS", contracts=2, slug="uzs-shop")
    _seed(db, currency="USD", contracts=3, slug="usd-shop",
          tenant="22222222-2222-2222-2222-222222222222")
    _alembic(TARGET)
    assert _distribution(db, "installments") == {"UZS": 2, "USD": 3}


def test_a_payment_takes_its_parent_contracts_currency(db) -> None:
    """Not the tenant's setting re-read — the parent row."""
    from sqlalchemy import text

    _seed(db, currency="USD", contracts=2, slug="usd-shop")
    _alembic(TARGET)
    with db.connect() as c:
        mismatched = c.execute(text(
            "SELECT count(*) FROM payments p JOIN installments i ON i.id = p.installment_id "
            "WHERE p.currency IS DISTINCT FROM i.currency"
        )).scalar()
    assert mismatched == 0
    assert _distribution(db, "payments") == {"USD": 2}


def test_a_change_audited_before_any_contract_is_not_a_conflict(db) -> None:
    """Contracts written after the last switch ARE the current currency.

    Blocking those would refuse a deploy for a tenant whose data is fine.
    """
    _seed(db, currency="USD", contracts=2)
    _audit_currency_change(db, tenant="11111111-1111-1111-1111-111111111111",
                           when="now() - interval '10 days'")
    _alembic(TARGET)
    assert _revision(db) == TARGET
    assert _distribution(db, "installments") == {"USD": 2}


# ── the documented default ──────────────────────────────────────────────────

def test_a_tenant_with_no_settings_row_defaults_to_uzs(db) -> None:
    _seed(db, currency=None, contracts=2, with_settings=False)
    _alembic(TARGET)
    assert _distribution(db, "installments") == {"UZS": 2}


def test_a_blank_configured_currency_is_unconfigured_not_unsupported(db) -> None:
    """`''` means never chosen, so it takes the default rather than aborting."""
    _seed(db, currency="   ", contracts=2)
    _alembic(TARGET)
    assert _distribution(db, "installments") == {"UZS": 2}


# ── it fails closed ─────────────────────────────────────────────────────────

def test_a_currency_change_with_earlier_contracts_aborts(db) -> None:
    """The relabelling case: contracts predate the switch."""
    _seed(db, currency="USD", contracts=3)
    _audit_currency_change(db, tenant="11111111-1111-1111-1111-111111111111",
                           when="now() + interval '1 day'")
    with pytest.raises(RuntimeError) as ex:
        _alembic(TARGET)
    msg = str(ex.value)
    assert "changed currency after" in msg
    assert "11111111-1111-1111-1111-111111111111" in msg
    assert "contracts_predating_change=3" in msg


@pytest.mark.parametrize("bad", ["EUR", "RUB", "uzs2", "€", "US"])
def test_an_unsupported_configured_currency_aborts(db, bad) -> None:
    """It is NEVER coerced to UZS — that would relabel real money."""
    _seed(db, currency=bad, contracts=2)
    with pytest.raises(RuntimeError) as ex:
        _alembic(TARGET)
    msg = str(ex.value)
    assert "outside" in msg
    assert "Refusing to coerce" in msg
    assert "11111111-1111-1111-1111-111111111111" in msg


@pytest.mark.parametrize("sloppy", ["usd ", " UZS", "uzs", "  Usd  "])
def test_case_and_padding_are_normalised_not_rejected(db, sloppy) -> None:
    """A supported code typed sloppily is still supported — trim and upper it."""
    _seed(db, currency=sloppy, contracts=1)
    _alembic(TARGET)
    assert _distribution(db, "installments") == {sloppy.strip().upper(): 1}


def test_an_unsupported_currency_aborts_even_with_no_contracts(db) -> None:
    """It would decide the tenant's NEXT contract, and the CHECK would reject it."""
    _seed(db, currency="EUR", contracts=0)
    with pytest.raises(RuntimeError):
        _alembic(TARGET)


# ── an abort leaves nothing behind ──────────────────────────────────────────

@pytest.mark.parametrize(
    ("currency", "audit_when"),
    [("EUR", None), ("USD", "now() + interval '1 day'")],
)
def test_a_failed_migration_leaves_the_previous_revision_and_no_data(db, currency, audit_when) -> None:
    from sqlalchemy import text

    _seed(db, currency=currency, contracts=2)
    if audit_when:
        _audit_currency_change(db, tenant="11111111-1111-1111-1111-111111111111",
                               when=audit_when)
    with pytest.raises(RuntimeError):
        _alembic(TARGET)

    assert _revision(db) == PREVIOUS, "the version bump must roll back with the DDL"
    assert not _has_currency_columns(db), "a partial column would survive a rerun"
    with db.connect() as c:
        for name in ("ck_installments_currency", "ck_payments_currency",
                     "uq_installments_id_currency", "fk_payments_contract_currency"):
            assert c.execute(text(
                "SELECT count(*) FROM pg_constraint WHERE conname = :n"
            ), {"n": name}).scalar() == 0, name
        # The rows themselves are untouched — this migration never deletes.
        assert c.execute(text("SELECT count(*) FROM installments")).scalar() == 2


def test_the_abort_carries_operational_detail_but_no_customer_pii(db) -> None:
    """Tenant ids and counts are actionable; a customer's name or phone is not."""
    _seed(db, currency="EUR", contracts=1)
    with pytest.raises(RuntimeError) as ex:
        _alembic(TARGET)
    msg = str(ex.value)
    assert "tenant=" in msg and "contracts=" in msg
    for leaked in ("+9989", "full_name", "phone", "passport", "jshshir"):
        assert leaked not in msg
    # Tenant UUIDs are operational identifiers, not PII — strip them, then check
    # nothing PAN- or phone-shaped is left in the message.
    without_ids = re.sub(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", "<tenant>", msg)
    assert not re.search(r"\d{7,}", without_ids), without_ids


# ── the chain itself stays healthy ──────────────────────────────────────────

def test_downgrade_and_reupgrade_are_clean(db) -> None:
    _seed(db, currency="UZS", contracts=2)
    _alembic(TARGET)
    assert _distribution(db, "installments") == {"UZS": 2}

    _alembic_down(PREVIOUS)
    assert _revision(db) == PREVIOUS
    assert not _has_currency_columns(db)

    _alembic(TARGET)
    assert _revision(db) == TARGET
    assert _distribution(db, "installments") == {"UZS": 2}
    assert _distribution(db, "payments") == {"UZS": 2}


def test_the_chain_has_exactly_one_head_and_this_revision_is_in_it(db) -> None:
    """One head, and this migration still on the path to it.

    Asserting the head IS this revision would break on every later migration,
    which teaches people to edit the assertion rather than read it.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    heads = script.get_heads()
    assert len(heads) == 1, heads
    lineage = {rev.revision for rev in script.iterate_revisions(heads[0], "base")}
    assert TARGET in lineage
