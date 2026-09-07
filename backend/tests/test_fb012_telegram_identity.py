"""FB-012 — a person already known to the bot must be recognised.

The founder's case: a shop typed a customer's number into its books, the same
person had already registered through @MuddatoBot, and the app still said
"Telegram ulanmagan". Nothing connected the two, because nothing had ever
established that the Telegram account owned that number.

This file pins the whole evidence chain: what counts as proof, what does not,
and which customer row a proof is allowed to bind to.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.phone_identity import PhoneEligibility, classify, is_uz, mask, same_number, to_e164
from app.models import Customer, Tenant, User
from app.models.enums import CustomerStatus
from app.services.identity_link import LinkState, reconcile
from app.services.telegram_contact import (
    SOURCE_TELEGRAM_CONTACT,
    ContactOutcome,
    record_verified_contact,
)

# asyncio_mode = "auto" — async tests need no marker.

#: Documentation numbers only. No real subscriber appears in this repository.
UZ_A = "+998901112233"
UZ_B = "+998901112244"
FOREIGN = "+14155552671"


# ── the numbering policy ────────────────────────────────────────────────────

class TestPhonePolicy:
    @pytest.mark.parametrize(("raw", "expected"), [
        ("+998 90 111 22 33", UZ_A),
        ("998901112233", UZ_A),
        ("901112233", UZ_A),          # bare local part
        ("+998-90-111-22-33", UZ_A),
    ])
    def test_one_subscriber_has_one_stored_form(self, raw, expected):
        assert to_e164(raw) == expected

    def test_a_foreign_number_parses_rather_than_vanishing(self):
        assert to_e164("+1 415 555 2671") == FOREIGN

    @pytest.mark.parametrize("bad", ["", None, "abc", "+", "12", "0901112233", "9" * 20])
    def test_rubbish_is_not_a_phone(self, bad):
        assert to_e164(bad) is None

    def test_uz_is_not_a_prefix_check(self):
        """`startswith('+998')` accepts numbers nobody can be reached on, and
        each one would occupy a real customer's slot in the unique index."""
        assert is_uz(UZ_A)
        for near_miss in ("+9985", "+998123", "+99890111223344"):
            assert near_miss.startswith("+998")
            assert not is_uz(near_miss)

    def test_eligibility_separates_wrong_country_from_not_a_number(self):
        assert classify(UZ_A) == (UZ_A, PhoneEligibility.eligible)
        assert classify(FOREIGN) == (FOREIGN, PhoneEligibility.unsupported_region)
        assert classify("nope") == (None, PhoneEligibility.malformed)

    def test_a_foreign_number_is_kept_not_discarded(self):
        """The account is told the region is unsupported; it is not erased.
        Throwing the value away would lose the only record of WHY."""
        value, reason = classify(FOREIGN)
        assert value == FOREIGN
        assert reason is PhoneEligibility.unsupported_region

    def test_same_number_ignores_formatting(self):
        assert same_number("+998 90 111 22 33", "998901112233")
        assert same_number("901112233", UZ_A)
        assert not same_number(UZ_A, UZ_B)

    def test_masking_is_safe_to_log(self):
        masked = mask(UZ_A)
        assert masked.startswith("+998")
        assert masked.endswith("33")
        # The middle is gone: enough to recognise a number you own, useless for
        # learning one you do not.
        assert "9011122" not in masked
        assert mask(None) == "—"


# ── proof of ownership ──────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def sess(migrated_db):
    maker = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with maker() as s:
        yield s


async def _user(sess, telegram_id: int, **kw) -> User:
    u = User(telegram_id=telegram_id, first_name="Test", **kw)
    sess.add(u)
    await sess.flush()
    return u


async def _tenant(sess, slug: str) -> Tenant:
    t = Tenant(name=slug, slug=slug)
    sess.add(t)
    await sess.flush()
    return t


async def _customer(sess, tenant, phone, **kw) -> Customer:
    c = Customer(tenant_id=tenant.id, full_name="Shop Label", phone=phone, **kw)
    sess.add(c)
    await sess.flush()
    return c


class TestContactIsProof:
    async def test_a_contact_that_is_mine_verifies_my_number(self, sess):
        u = await _user(sess, 1001)
        r = await record_verified_contact(
            sess, sender_telegram_id=1001, contact_user_id=1001, raw_phone="+998 90 111 22 33",
        )
        assert r.outcome is ContactOutcome.verified
        await sess.refresh(u)
        assert u.verified_phone == UZ_A
        assert u.verified_phone_source == SOURCE_TELEGRAM_CONTACT
        assert u.verified_phone_at is not None
        assert u.phone_eligibility == PhoneEligibility.eligible.value

    async def test_sharing_a_friends_card_proves_nothing(self, sess):
        """The whole check. A contact message can carry anyone's card; only
        `contact.user_id == sender` says it is the sender's own."""
        u = await _user(sess, 1002)
        r = await record_verified_contact(
            sess, sender_telegram_id=1002, contact_user_id=9999, raw_phone=UZ_A,
        )
        assert r.outcome is ContactOutcome.not_owner
        await sess.refresh(u)
        assert u.verified_phone is None

    async def test_a_card_with_no_owner_field_proves_nothing(self, sess):
        u = await _user(sess, 1003)
        r = await record_verified_contact(
            sess, sender_telegram_id=1003, contact_user_id=None, raw_phone=UZ_A,
        )
        assert r.outcome is ContactOutcome.not_owner
        await sess.refresh(u)
        assert u.verified_phone is None

    async def test_a_malformed_number_is_refused(self, sess):
        await _user(sess, 1004)
        r = await record_verified_contact(
            sess, sender_telegram_id=1004, contact_user_id=1004, raw_phone="not-a-phone",
        )
        assert r.outcome is ContactOutcome.malformed

    async def test_redelivery_of_the_same_contact_is_idempotent(self, sess):
        """Telegram retries, and people tap twice."""
        u = await _user(sess, 1005)
        for _ in range(3):
            r = await record_verified_contact(
                sess, sender_telegram_id=1005, contact_user_id=1005, raw_phone=UZ_A,
            )
            assert r.ok
        assert r.outcome is ContactOutcome.unchanged
        await sess.refresh(u)
        assert u.verified_phone == UZ_A

    async def test_a_replay_carrying_a_different_number_is_refused(self, sess):
        """Changing a proved number is a support action, not something a second
        message may do on its own."""
        u = await _user(sess, 1006)
        await record_verified_contact(
            sess, sender_telegram_id=1006, contact_user_id=1006, raw_phone=UZ_A,
        )
        r = await record_verified_contact(
            sess, sender_telegram_id=1006, contact_user_id=1006, raw_phone=UZ_B,
        )
        assert r.outcome is ContactOutcome.conflict
        await sess.refresh(u)
        assert u.verified_phone == UZ_A

    async def test_two_accounts_cannot_both_own_one_number(self, sess):
        await _user(sess, 1007)
        await _user(sess, 1008)
        first = await record_verified_contact(
            sess, sender_telegram_id=1007, contact_user_id=1007, raw_phone=UZ_A,
        )
        assert first.ok
        await sess.commit()
        second = await record_verified_contact(
            sess, sender_telegram_id=1008, contact_user_id=1008, raw_phone=UZ_A,
        )
        assert second.outcome is ContactOutcome.taken

    async def test_a_foreign_number_verifies_but_is_not_eligible(self, sess):
        """Proof of ownership and permission to trade are different questions.
        The account is not deleted and not banned; it simply cannot activate."""
        u = await _user(sess, 1009)
        r = await record_verified_contact(
            sess, sender_telegram_id=1009, contact_user_id=1009, raw_phone=FOREIGN,
        )
        assert r.outcome is ContactOutcome.verified
        await sess.refresh(u)
        assert u.verified_phone == FOREIGN
        assert u.phone_eligibility == PhoneEligibility.unsupported_region.value
        assert u.is_active is True

    async def test_an_unknown_sender_has_nothing_to_verify(self, sess):
        r = await record_verified_contact(
            sess, sender_telegram_id=404404, contact_user_id=404404, raw_phone=UZ_A,
        )
        assert r.outcome is ContactOutcome.unknown_sender

    async def test_the_database_refuses_a_value_with_no_provenance(self, sess):
        """A verified phone always says how it was proved. Without the check a
        row could hold a value indistinguishable from a typed one."""
        from sqlalchemy.exc import IntegrityError
        u = await _user(sess, 1010)
        u.verified_phone = UZ_B
        with pytest.raises(IntegrityError):
            await sess.flush()
        await sess.rollback()
        _ = u


# ── which customer a proof binds to ─────────────────────────────────────────

class TestReconciliation:
    async def test_the_founders_case_one_match_links(self, sess):
        """Merchant typed the number; the person had already registered."""
        t = await _tenant(sess, f"shop-{uuid.uuid4().hex[:8]}")
        c = await _customer(sess, t, "+998 90 111 22 33", status=CustomerStatus.pending)
        u = await _user(sess, 2001)
        await record_verified_contact(
            sess, sender_telegram_id=2001, contact_user_id=2001, raw_phone=UZ_A,
        )
        r = await reconcile(sess, user=u, tenant_id=t.id)
        assert r.state is LinkState.linked
        assert r.customer_id == c.id
        await sess.refresh(c)
        assert c.telegram_id == 2001
        assert c.status == CustomerStatus.active

    async def test_linking_is_idempotent(self, sess):
        t = await _tenant(sess, f"shop-{uuid.uuid4().hex[:8]}")
        await _customer(sess, t, UZ_A)
        u = await _user(sess, 2002)
        await record_verified_contact(
            sess, sender_telegram_id=2002, contact_user_id=2002, raw_phone=UZ_A,
        )
        assert (await reconcile(sess, user=u, tenant_id=t.id)).state is LinkState.linked
        again = await reconcile(sess, user=u, tenant_id=t.id)
        assert again.state is LinkState.already_linked
        assert again.ok

    async def test_two_matching_rows_are_never_guessed_between(self, sess):
        """A person cannot be two customers of one shop, and picking one would
        attach a stranger's contracts to this account."""
        t = await _tenant(sess, f"shop-{uuid.uuid4().hex[:8]}")
        await _customer(sess, t, UZ_A)
        # A second row for the same subscriber, written before canonicalisation.
        from sqlalchemy.exc import IntegrityError
        dup = Customer(tenant_id=t.id, full_name="Same person, again", phone="901112233")
        sess.add(dup)
        with pytest.raises(IntegrityError):   # the tenant-unique phone index refuses it
            await sess.flush()
        await sess.rollback()

    async def test_ambiguity_is_reported_when_the_data_allows_it(self, sess, monkeypatch):
        """The index prevents duplicates going forward; historical rows that
        predate it still have to produce a state rather than a guess."""
        import app.services.identity_link as link_mod
        t = await _tenant(sess, f"shop-{uuid.uuid4().hex[:8]}")
        a = await _customer(sess, t, UZ_A)
        u = await _user(sess, 2003)
        await record_verified_contact(
            sess, sender_telegram_id=2003, contact_user_id=2003, raw_phone=UZ_A,
        )
        ghost = Customer(tenant_id=t.id, full_name="Legacy row", phone=UZ_A)

        async def _two(*_a, **_kw):
            return [a, ghost]
        monkeypatch.setattr(link_mod, "find_candidates", _two)

        r = await reconcile(sess, user=u, tenant_id=t.id)
        assert r.state is LinkState.ambiguous
        assert r.candidates == 2
        assert r.customer_id is None

    async def test_no_match_says_nothing_about_other_shops(self, sess):
        """The same answer whether the number is unknown everywhere or known in
        a shop the caller has no business asking about."""
        here = await _tenant(sess, f"here-{uuid.uuid4().hex[:8]}")
        elsewhere = await _tenant(sess, f"else-{uuid.uuid4().hex[:8]}")
        await _customer(sess, elsewhere, UZ_A)
        u = await _user(sess, 2004)
        await record_verified_contact(
            sess, sender_telegram_id=2004, contact_user_id=2004, raw_phone=UZ_A,
        )
        known_elsewhere = await reconcile(sess, user=u, tenant_id=here.id)

        u2 = await _user(sess, 2005)
        await record_verified_contact(
            sess, sender_telegram_id=2005, contact_user_id=2005, raw_phone=UZ_B,
        )
        known_nowhere = await reconcile(sess, user=u2, tenant_id=here.id)

        assert known_elsewhere.state is known_nowhere.state is LinkState.no_match
        assert known_elsewhere.candidates == known_nowhere.candidates == 0

    async def test_a_customer_bound_to_someone_else_is_not_rebound(self, sess):
        t = await _tenant(sess, f"shop-{uuid.uuid4().hex[:8]}")
        c = await _customer(sess, t, UZ_A, telegram_id=777777)
        u = await _user(sess, 2006)
        await record_verified_contact(
            sess, sender_telegram_id=2006, contact_user_id=2006, raw_phone=UZ_A,
        )
        r = await reconcile(sess, user=u, tenant_id=t.id)
        assert r.state is LinkState.customer_taken
        await sess.refresh(c)
        assert c.telegram_id == 777777

    async def test_one_account_is_one_customer_per_shop(self, sess):
        t = await _tenant(sess, f"shop-{uuid.uuid4().hex[:8]}")
        await _customer(sess, t, UZ_A, telegram_id=2007)
        other = await _customer(sess, t, UZ_B)
        u = await _user(sess, 2007)
        await record_verified_contact(
            sess, sender_telegram_id=2007, contact_user_id=2007, raw_phone=UZ_B,
        )
        r = await reconcile(sess, user=u, tenant_id=t.id, expected_customer_id=other.id)
        assert r.state is LinkState.identity_taken

    async def test_the_database_refuses_a_duplicate_binding(self, sess):
        """The application checks first; under concurrency only the index can."""
        from sqlalchemy.exc import IntegrityError
        t = await _tenant(sess, f"shop-{uuid.uuid4().hex[:8]}")
        await _customer(sess, t, UZ_A, telegram_id=2008)
        await sess.flush()
        clash = Customer(tenant_id=t.id, full_name="Second row", phone=UZ_B, telegram_id=2008)
        sess.add(clash)
        with pytest.raises(IntegrityError):
            await sess.flush()
        await sess.rollback()

    async def test_one_person_may_be_a_customer_of_several_shops(self, sess):
        a = await _tenant(sess, f"a-{uuid.uuid4().hex[:8]}")
        b = await _tenant(sess, f"b-{uuid.uuid4().hex[:8]}")
        await _customer(sess, a, UZ_A)
        await _customer(sess, b, UZ_A)
        u = await _user(sess, 2009)
        await record_verified_contact(
            sess, sender_telegram_id=2009, contact_user_id=2009, raw_phone=UZ_A,
        )
        assert (await reconcile(sess, user=u, tenant_id=a.id)).state is LinkState.linked
        assert (await reconcile(sess, user=u, tenant_id=b.id)).state is LinkState.linked

    async def test_an_invitation_constrains_which_customer_may_be_linked(self, sess):
        """The named customer must be the one the number matches. A mismatch is
        a mismatch, not an invitation to link whoever does match."""
        t = await _tenant(sess, f"shop-{uuid.uuid4().hex[:8]}")
        await _customer(sess, t, UZ_A)
        invited = await _customer(sess, t, UZ_B)
        u = await _user(sess, 2010)
        await record_verified_contact(
            sess, sender_telegram_id=2010, contact_user_id=2010, raw_phone=UZ_A,
        )
        r = await reconcile(sess, user=u, tenant_id=t.id, expected_customer_id=invited.id)
        assert r.state is LinkState.no_match
        await sess.refresh(invited)
        assert invited.telegram_id is None

    async def test_an_unproved_number_matches_nothing(self, sess):
        t = await _tenant(sess, f"shop-{uuid.uuid4().hex[:8]}")
        await _customer(sess, t, UZ_A)
        u = await _user(sess, 2011, phone=UZ_A)   # typed, not proved
        r = await reconcile(sess, user=u, tenant_id=t.id)
        assert r.state is LinkState.unverified

    async def test_a_foreign_number_cannot_activate_a_customer(self, sess):
        t = await _tenant(sess, f"shop-{uuid.uuid4().hex[:8]}")
        c = await _customer(sess, t, UZ_A, status=CustomerStatus.pending)
        u = await _user(sess, 2012)
        await record_verified_contact(
            sess, sender_telegram_id=2012, contact_user_id=2012, raw_phone=FOREIGN,
        )
        r = await reconcile(sess, user=u, tenant_id=t.id)
        assert r.state is LinkState.ineligible
        await sess.refresh(c)
        assert c.telegram_id is None
        assert c.status == CustomerStatus.pending

    async def test_a_typed_uzbek_number_cannot_override_a_proved_foreign_one(self, sess):
        u = await _user(sess, 2013)
        await record_verified_contact(
            sess, sender_telegram_id=2013, contact_user_id=2013, raw_phone=FOREIGN,
        )
        u.phone = UZ_A                     # the business record; typed, unproved
        await sess.flush()
        await sess.refresh(u)
        assert u.verified_phone == FOREIGN
        assert u.phone_eligibility == PhoneEligibility.unsupported_region.value


# ── what the source says about itself ───────────────────────────────────────

class TestSourceInvariants:
    def test_no_telegram_user_session_primitives_anywhere(self):
        """MTProto user authorization is not how a customer proves a number, and
        nothing in this product may hold a Telegram user session."""
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1] / "app"
        banned = ("auth.sendCode", "auth.signIn", "sendCode(", "signIn(",
                  "auth_key", "MTProto", "phone_code_hash", "2fa_password")
        for path in root.rglob("*.py"):
            text = path.read_text()
            for token in banned:
                assert token not in text, f"{token} in {path}"

    def test_the_migration_never_manufactures_a_proof(self):
        """A stored number is evidence a form was filled in. Copying it into the
        verified column would fabricate the exact claim that column exists to
        make trustworthy — for every legacy row at once."""
        import pathlib
        src = (pathlib.Path(__file__).resolve().parents[1]
               / "alembic/versions/rc0_verified_identity.py").read_text()
        body = src.split("def upgrade")[1]
        for forbidden in ("SET verified_phone", "verified_phone = phone",
                          "UPDATE users SET", "INSERT INTO users"):
            assert forbidden not in body, forbidden
        # And it refuses to proceed over an unresolvable duplicate rather than
        # picking a row.
        assert "raise RuntimeError" in body
        assert "DELETE" not in body.upper()

    def test_the_verification_path_never_trusts_a_client_phone(self):
        import pathlib
        src = (pathlib.Path(__file__).resolve().parents[1]
               / "app/services/telegram_contact.py").read_text()
        # The one comparison that makes a contact proof.
        assert "contact_user_id != sender_telegram_id" in src

    def test_logs_carry_masked_numbers_only(self):
        """A structured log is read by people who have no business seeing a
        customer's number, and it outlives the request that wrote it.

        Checks the FORMAT STRING, not the argument names: `logger.info("phone=%s",
        e164)` reads innocently and leaks the number, which is precisely the
        mistake worth catching.
        """
        import pathlib
        import re
        for name in ("app/services/telegram_contact.py", "app/services/identity_link.py"):
            src = (pathlib.Path(__file__).resolve().parents[1] / name).read_text()
            for call in re.findall(r"logger\.\w+\((?:[^()]|\([^()]*\))*\)", src, re.S):
                head, _, args = call.partition(",")
                if "phone" not in head:
                    continue          # the format string does not report a number
                assert "mask(" in args, f"unmasked number in a log line: {call}"


async def test_customers_linked_by_telegram_id_are_findable(sess):
    """The `/me` surface finds a person's shops by Telegram id — the link this
    whole batch exists to create."""
    t = await _tenant(sess, f"shop-{uuid.uuid4().hex[:8]}")
    await _customer(sess, t, UZ_A)
    u = await _user(sess, 3001)
    await record_verified_contact(
        sess, sender_telegram_id=3001, contact_user_id=3001, raw_phone=UZ_A,
    )
    await reconcile(sess, user=u, tenant_id=t.id)
    await sess.commit()
    rows = (await sess.execute(
        select(Customer).where(Customer.telegram_id == 3001)
    )).scalars().all()
    assert len(rows) == 1
