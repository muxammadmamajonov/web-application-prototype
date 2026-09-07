"""A name the person saved has to survive their next sign-in.

`display_name` existed and `person_name()` preferred it, but no endpoint ever
wrote it: the profile screen saved into `first_name` / `last_name`, which
Telegram owns and refreshes from validated initData on the next login. So the
founder could type "Example User", watch it save, and be greeted as
"Muddato Support" again the moment they reopened the app.

The scenario in `test_the_exact_founder_regression` is that sequence, start to
finish.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.auth import apply_telegram_identity
from app.models import User
from app.models.enums import PlatformRole
from app.services.display_names import MAX_DISPLAY_NAME, normalize_display_name, person_name

OPERATOR = "Muddato Support"
CHOSEN = "Example User"


@pytest_asyncio.fixture
async def api(migrated_db):
    import httpx
    from httpx import ASGITransport

    from app.db import get_db
    from app.main import app

    maker = async_sessionmaker(migrated_db, expire_on_commit=False)

    async def _override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = _override
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c, maker() as s:
        yield c, s
    app.dependency_overrides.pop(get_db, None)


def _auth(user: User) -> dict:
    from app.core.security import issue_token
    return {"Authorization": f"Bearer {issue_token(user_id=str(user.id))}"}


class TestNormalization:
    @pytest.mark.parametrize(("raw", "expected"), [
        ("  Example User  ", CHOSEN),
        ("Example\tUser", CHOSEN),
        ("Дилноза Каримова", "Дилноза Каримова"),
        ("Oʻktam Gʻaniyev", "Oʻktam Gʻaniyev"),   # Uzbek Latin U+02BB
    ])
    def test_whitespace_is_collapsed_and_letters_are_kept(self, raw, expected):
        assert normalize_display_name(raw) == expected

    def test_case_is_preserved(self):
        assert normalize_display_name("mcDonald o'Brien") == "mcDonald o'Brien"

    @pytest.mark.parametrize("empty", [None, "", "   ", "​​", "‮"])
    def test_an_invisible_only_name_is_no_name(self, empty):
        """It would render as an empty greeting while the record looked filled in."""
        assert normalize_display_name(empty) is None

    def test_it_is_capped_at_the_column_width(self):
        out = normalize_display_name("A" * 500)
        assert out is not None and len(out) == MAX_DISPLAY_NAME

    def test_normalization_is_server_side(self):
        """The client may send anything; what lands in the column is decided here."""
        import inspect

        from app.api.auth import update_me
        assert "normalize_display_name" in inspect.getsource(update_me)


class TestPersistence:
    async def test_saving_a_name_stores_it_where_telegram_cannot_reach(self, api):
        client, sess = api
        u = User(telegram_id=51001, first_name=OPERATOR, locale="uz")
        sess.add(u)
        await sess.commit()

        r = await client.patch("/api/auth/me", headers=_auth(u),
                               json={"display_name": f"  {CHOSEN}  "})
        assert r.status_code == 200, r.text
        assert r.json()["displayName"] == CHOSEN

        await sess.refresh(u)
        assert u.display_name == CHOSEN
        # Telegram's own fields are untouched by a profile edit.
        assert u.first_name == OPERATOR

    async def test_the_exact_founder_regression(self, api):
        """Telegram profile says "Muddato Support"; a platform role exists; the
        person saves their own name; a later sign-in refreshes Telegram's fields;
        the greeting still says what they chose."""
        client, sess = api
        u = User(telegram_id=51002, first_name=OPERATOR,
                 platform_role=PlatformRole.support, locale="uz")
        sess.add(u)
        await sess.commit()

        # Before: no personal name, so no personalisation at all.
        assert person_name(u) is None

        r = await client.patch("/api/auth/me", headers=_auth(u), json={"display_name": CHOSEN})
        assert r.status_code == 200
        await sess.refresh(u)

        # A later validated sign-in refreshes what Telegram owns…
        apply_telegram_identity(u, username="muddato_support", first_name=OPERATOR,
                                last_name=None, photo_url=None)
        await sess.commit()
        await sess.refresh(u)

        assert u.first_name == OPERATOR          # Telegram still owns this
        assert person_name(u) == CHOSEN          # …and the greeting does not use it
        body = (await client.get("/api/auth/me", headers=_auth(u))).json()
        assert body["displayName"] == CHOSEN
        assert body["firstName"] == OPERATOR     # the profile screen may still show it

    async def test_clearing_the_name_returns_to_the_telegram_default(self, api):
        client, sess = api
        u = User(telegram_id=51003, first_name="Dilnoza", last_name="Karimova", locale="uz")
        sess.add(u)
        await sess.commit()
        await client.patch("/api/auth/me", headers=_auth(u), json={"display_name": "Dilya"})
        await sess.refresh(u)
        assert person_name(u) == "Dilya"
        await client.patch("/api/auth/me", headers=_auth(u), json={"display_name": ""})
        await sess.refresh(u)
        assert u.display_name is None
        assert person_name(u) == "Dilnoza Karimova"

    async def test_the_profile_cannot_write_telegram_owned_names(self, api):
        """Accepting them would be accepting a value the next login overwrites."""
        client, sess = api
        u = User(telegram_id=51004, first_name="Original", locale="uz")
        sess.add(u)
        await sess.commit()
        r = await client.patch("/api/auth/me", headers=_auth(u),
                               json={"first_name": "Typed", "last_name": "Typed"})
        assert r.status_code in (200, 422)
        await sess.refresh(u)
        assert u.first_name == "Original"


class TestMassAssignment:
    @pytest.mark.parametrize("field", [
        "verified_phone", "verified_phone_source", "verified_phone_at",
        "phone_eligibility", "telegram_id", "platform_role",
    ])
    async def test_identity_columns_are_not_client_writable(self, api, field):
        client, sess = api
        u = User(telegram_id=52001, first_name="X", locale="uz")
        sess.add(u)
        await sess.commit()
        before = getattr(u, field)
        r = await client.patch("/api/auth/me", headers=_auth(u),
                               json={field: "+998901112233" if "phone" in field else 999})
        assert r.status_code in (200, 422)
        await sess.refresh(u)
        assert getattr(u, field) == before, field

    async def test_the_profile_cannot_set_a_second_phone_at_all(self, api):
        """This asserted the opposite until the phone-identity normalisation.

        It checked that editing the "contact phone" moved `users.phone` while
        leaving `verified_phone` alone — which was true, and was the problem.
        Two writable phone columns for one person meant the product asked for
        two numbers, rendered both on the Profile screen, and had no single
        answer to "what is this account's phone?". The typed one proved nothing
        and reached nobody.

        There is now one account phone, it is the proved one, and the profile
        form cannot write a number that is not it.
        """
        from app.services.telegram_contact import record_verified_contact
        client, sess = api
        u = User(telegram_id=52002, first_name="X", locale="uz")
        sess.add(u)
        await sess.flush()
        await record_verified_contact(sess, sender_telegram_id=52002,
                                      contact_user_id=52002, raw_phone="+998901112233")
        await sess.commit()

        r = await client.patch("/api/auth/me", headers=_auth(u),
                               json={"phone": "+998901112244"})
        # Refused, not silently dropped: a 200 that saves nothing is how "my
        # number won't change" becomes a support ticket instead of an answer.
        assert r.status_code == 422
        assert r.json()["detail"] == "phone_not_editable"
        await sess.refresh(u)
        assert u.phone is None                      # no second number was minted
        assert u.verified_phone == "+998901112233"  # the proof is untouched

    async def test_an_echo_of_the_proved_number_is_a_no_op_not_a_rejection(self, api):
        """A client round-tripping the profile it was served must still save."""
        from app.services.telegram_contact import record_verified_contact
        client, sess = api
        u = User(telegram_id=52003, first_name="X", locale="uz")
        sess.add(u)
        await sess.flush()
        await record_verified_contact(sess, sender_telegram_id=52003,
                                      contact_user_id=52003, raw_phone="+998901112233")
        await sess.commit()

        r = await client.patch("/api/auth/me", headers=_auth(u),
                               json={"phone": "+998 90 111 22 33", "email": "x@y.uz"})
        assert r.status_code == 200, r.text
        await sess.refresh(u)
        assert u.email == "x@y.uz"
        assert u.verified_phone == "+998901112233"

    async def test_a_changed_verified_number_is_not_a_profile_edit(self, api):
        """It is a support workflow. Nothing on the profile surface can do it."""
        from app.api.auth import ProfileUpdateIn
        assert "verified_phone" not in ProfileUpdateIn.model_fields
        assert "phone_eligibility" not in ProfileUpdateIn.model_fields
