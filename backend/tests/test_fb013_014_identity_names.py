"""FB-013 / FB-014 — which name is authoritative, and who may be greeted by it.

FB-014 as observed: the customer record said "Example User" and the home
screen said "Salom, Muddato Support". Both strings were real; the screen was
reading the wrong one. The signed-in account's TELEGRAM profile name went
straight to the greeting, and that account is the platform's support identity.

FB-013 is the rule that prevents it recurring: four names exist for one person
and each surface has exactly one correct source.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Customer, Tenant, User
from app.models.enums import PlatformRole
from app.services.display_names import contract_party_name, merchant_label, person_name

#: The exact pair from the screenshot.
OPERATOR_TELEGRAM_NAME = "Muddato Support"
CUSTOMER_RECORD_NAME = "Example User"


def _user(**kw) -> User:
    return User(telegram_id=kw.pop("telegram_id", 1), **kw)


class TestGreetingSource:
    def test_the_screenshot_does_not_reproduce(self):
        """A support account browsing the client surface is not greeted by its
        operator name — the single defect FB-014 reported."""
        operator = _user(
            first_name=OPERATOR_TELEGRAM_NAME,
            platform_role=PlatformRole.support,
        )
        assert person_name(operator) != OPERATOR_TELEGRAM_NAME
        assert person_name(operator) is None

    def test_a_merchants_label_is_never_the_greeting(self):
        """`Customer.full_name` is the shop's own note. Shops write "Aka Jasur"
        and "Jasur — telefon" in it; it is not what to call someone."""
        person = _user(first_name="Jasur")
        customer = Customer(tenant_id=uuid.uuid4(), full_name="Aka Jasur (telefon)",
                            phone="+998901112233")
        assert person_name(person) == "Jasur"
        assert merchant_label(customer) == "Aka Jasur (telefon)"
        assert person_name(person) != merchant_label(customer)

    def test_an_ordinary_customer_is_greeted_by_their_telegram_name(self):
        """The documented default when the person has chosen nothing."""
        person = _user(first_name="Dilnoza", last_name="Karimova")
        assert person_name(person) == "Dilnoza Karimova"

    def test_a_self_chosen_name_outranks_telegram(self):
        person = _user(first_name="Dilnoza", display_name="Dilya")
        assert person_name(person) == "Dilya"

    def test_a_self_chosen_name_rescues_an_operator_account(self):
        """An operator who is genuinely also a customer says so explicitly."""
        operator = _user(
            first_name=OPERATOR_TELEGRAM_NAME,
            platform_role=PlatformRole.support,
            display_name=CUSTOMER_RECORD_NAME,
        )
        assert person_name(operator) == CUSTOMER_RECORD_NAME

    def test_a_missing_name_is_absent_not_invented(self):
        """No phone, no @handle, no bot name standing in for a person."""
        assert person_name(_user()) is None
        assert person_name(_user(first_name="", last_name="")) is None
        assert person_name(_user(display_name="   ")) is None

    def test_the_username_is_never_a_name(self):
        person = _user(telegram_username="muxa_shop")
        assert person_name(person) is None

    def test_a_super_admin_is_treated_like_support(self):
        boss = _user(first_name="Platform Root", platform_role=PlatformRole.super_admin)
        assert person_name(boss) is None


class TestContractNames:
    def test_a_contract_is_issued_to_the_shops_record(self):
        c = Customer(tenant_id=uuid.uuid4(), full_name=CUSTOMER_RECORD_NAME,
                     phone="+998901112233")
        assert contract_party_name(c) == CUSTOMER_RECORD_NAME

    def test_renaming_a_person_does_not_rewrite_a_signed_document(self):
        """The resolver returns a value the caller snapshots. Nothing here reads
        a live profile, so a later profile edit cannot reach a signed contract."""
        import inspect
        src = inspect.getsource(contract_party_name)
        assert "User" not in src and "display_name" not in src


# ── the wire format the client renders ──────────────────────────────────────

@pytest_asyncio.fixture
async def sess(migrated_db):
    maker = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with maker() as s:
        yield s


class TestMePayload:
    async def test_me_carries_a_resolved_name_not_a_rule_to_re_derive(self, sess):
        """One place decides. A client that re-implemented the rule would drift
        from it, and this is the rule that keeps an operator's name off a
        customer's screen."""
        from app.schemas.auth import MeResponse

        operator = User(
            telegram_id=91001, first_name=OPERATOR_TELEGRAM_NAME,
            platform_role=PlatformRole.support, locale="uz",
        )
        sess.add(operator)
        await sess.flush()

        body = MeResponse(
            id=operator.id, telegram_id=operator.telegram_id,
            first_name=operator.first_name, display_name=person_name(operator),
            locale=operator.locale,
        ).model_dump(by_alias=True)

        assert body["displayName"] is None
        # firstName still carries the Telegram profile, because the profile
        # screen legitimately shows it — it is the GREETING that must not.
        assert body["firstName"] == OPERATOR_TELEGRAM_NAME

    async def test_a_customers_shops_are_found_by_their_telegram_link(self, sess):
        t = Tenant(name="Shop", slug=f"shop-{uuid.uuid4().hex[:8]}")
        sess.add(t)
        await sess.flush()
        sess.add(Customer(tenant_id=t.id, full_name=CUSTOMER_RECORD_NAME,
                          phone="+998901112233", telegram_id=91002))
        u = User(telegram_id=91002, first_name="Muxammad", locale="uz")
        sess.add(u)
        await sess.flush()
        # The read is gated on proved ownership, so the fixture proves it.
        from app.services.telegram_contact import record_verified_contact
        assert (await record_verified_contact(
            sess, sender_telegram_id=91002, contact_user_id=91002,
            raw_phone="+998901112233",
        )).ok

        from app.api.me import _my_customer_rows
        rows = await _my_customer_rows(sess, u)
        assert [c.full_name for c in rows] == [CUSTOMER_RECORD_NAME]


class TestSourceOfTruthIsDocumented:
    def test_the_resolver_is_the_only_greeting_source(self):
        """Nothing else may assemble a greeting name from raw profile fields."""
        import pathlib
        api = pathlib.Path(__file__).resolve().parents[1] / "app/api"
        for path in api.rglob("*.py"):
            text = path.read_text()
            for line in text.splitlines():
                if "MeResponse(" in line:
                    continue
                # The one composition pattern that produced the defect.
                assert 'f"{user.first_name} {user.last_name}"' not in line, path

    @pytest.mark.parametrize("banned", ["FB-01", "Batch 3", "Phase 9"])
    def test_no_audit_narrative_in_the_new_production_modules(self, banned):
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1]
        for name in (
            "app/services/display_names.py",
            "app/services/identity_link.py",
            "app/services/telegram_contact.py",
            "app/core/phone_identity.py",
        ):
            assert banned not in (root / name).read_text(), name
