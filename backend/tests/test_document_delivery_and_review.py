"""Who may receive a document, and who may act on a receipt.

Three separate claims, and they fail in different ways, so they are tested
separately rather than through one happy path:

  * **captions and names** — what arrives in a chat must be readable by the
    person who receives it and must not carry identity fields that outlive the
    document's access controls;
  * **idempotency** — one logical delivery per person per document, enforced by
    the database, because two concurrent requests both read "nothing sent yet";
  * **authorization** — a deep link travels in a group chat, so forwarding it
    must grant precisely nothing.

The intent tests are the ones to read first. A `startapp` parameter is public
by construction, and the entire design rests on it being a pointer rather than
a credential.
"""
from __future__ import annotations

import time
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio

from app.core.documents import _GROUP_SEP as NNBSP
from app.core.review_intent import (
    MAX_STARTAPP_LEN,
    PURPOSE_PAYMENT_REVIEW,
    InvalidIntent,
    mint,
    resolve,
)
from app.models import (
    Customer,
    DocumentDelivery,
    Installment,
    Payment,
    Tenant,
    TenantSettings,
    TenantUser,
    User,
)
from app.models.enums import PaymentMethod, PaymentStatus, TenantRole


@pytest_asyncio.fixture
async def db_sessions():
    import os

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import Base

    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")
    from sqlalchemy import text

    engine = create_async_engine(url)
    # DROP SCHEMA rather than `drop_all`: a previous migrated run leaves RLS
    # POLICIES behind, and a policy on one table depends on another, so
    # metadata-ordered drops cannot untangle them. Same approach as conftest's
    # `migrated_db`.
    async def _reset() -> None:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))

    await _reset()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await _reset()
        await engine.dispose()


async def _shop(sess, *, name="Demo Electronics", tg=90001):
    t = Tenant(name=name, slug=f"s-{uuid.uuid4().hex[:8]}")
    sess.add(t)
    await sess.flush()
    sess.add(TenantSettings(tenant_id=t.id, business_name=name))
    owner = User(telegram_id=tg)
    sess.add(owner)
    await sess.flush()
    sess.add(TenantUser(tenant_id=t.id, user_id=owner.id, role=TenantRole.owner))
    c = Customer(tenant_id=t.id, full_name="Aliyev Vali", phone="+998901112233",
                 passport_id="AA1234567", jshshir="30112900010001")
    sess.add(c)
    await sess.flush()
    inst = Installment(
        tenant_id=t.id, customer_id=c.id, currency="UZS",
        contract_number="MDD-2026-000001", start_date=date(2026, 9, 1),
        months=6, monthly_amount=Decimal("1000000"),
        total_amount=Decimal("6000000"), principal=Decimal("6000000"),
        down_payment=Decimal("0"),
    )
    sess.add(inst)
    await sess.flush()
    pay = Payment(
        tenant_id=t.id, installment_id=inst.id, customer_id=c.id, currency="UZS",
        amount=Decimal("4800000"), method=PaymentMethod.cash,
        status=PaymentStatus.pending,
        paid_at=datetime(2026, 9, 1, 9, 23, tzinfo=UTC),
    )
    sess.add(pay)
    await sess.flush()
    return t, owner, inst, pay


# ── captions ────────────────────────────────────────────────────────────────

class TestCaptions:
    async def test_a_contract_caption_is_readable_not_a_uuid(self, db_sessions):
        from app.services.document_delivery import contract_caption

        async with db_sessions() as sess:
            _t, _o, inst, _p = await _shop(sess)
            cap = await contract_caption(sess, inst.id, locale="uz")

        assert cap.startswith("📄 Shartnoma MDD-2026-000001")
        assert "Do'kon: Demo Electronics" in cap
        assert "Mijoz: Aliyev Vali" in cap
        assert f"6{NNBSP}000{NNBSP}000 so'm" in cap
        assert "1-sentabr, 2026" in cap
        assert str(inst.id) not in cap
        assert ".pdf" not in cap

    async def test_a_receipt_caption_carries_the_contract_and_localized_enums(
        self, db_sessions,
    ):
        from app.services.document_delivery import receipt_caption

        async with db_sessions() as sess:
            _t, _o, _i, pay = await _shop(sess)
            cap = await receipt_caption(sess, pay.id, locale="uz")

        assert cap.startswith("🧾 To'lov cheki")
        assert "Shartnoma: MDD-2026-000001" in cap
        assert "Naqd" in cap and "cash" not in cap
        assert "Kutilmoqda" in cap and "pending" not in cap
        assert "1-sentabr, 2026, 14:23" in cap

    async def test_no_identity_field_reaches_a_caption(self, db_sessions):
        # A caption is a chat message. It gets forwarded out of the shop, and it
        # outlives every access control the document itself has.
        from app.services.document_delivery import contract_caption, receipt_caption

        async with db_sessions() as sess:
            _t, _o, inst, pay = await _shop(sess)
            both = (await contract_caption(sess, inst.id)) + (await receipt_caption(sess, pay.id))

        for leak in ("AA1234567", "30112900010001", "+998901112233", "998901112233"):
            assert leak not in both, f"{leak!r} leaked into a caption"

    async def test_a_caption_degrades_rather_than_losing_the_document(self):
        # Telegram REJECTS a message whose caption is too long — the file never
        # arrives. Truncating is strictly better than that.
        from app.services.document_delivery import MAX_CAPTION, _clip

        out = _clip("x" * (MAX_CAPTION * 2))
        assert len(out) <= MAX_CAPTION

    async def test_both_locales_produce_a_caption(self, db_sessions):
        from app.services.document_delivery import receipt_caption

        async with db_sessions() as sess:
            _t, _o, _i, pay = await _shop(sess)
            ru = await receipt_caption(sess, pay.id, locale="ru")
        assert "Чек об оплате" in ru
        assert "Наличные" in ru and "cash" not in ru

    @pytest.mark.parametrize("raw,expected", [
        ("  Aliyev   Vali  ", "Aliyev Vali"),
        ("", "—"),
        (None, "—"),
    ])
    def test_a_display_name_is_trimmed_and_bounded(self, raw, expected):
        from app.services.document_delivery import safe_display_name

        assert safe_display_name(raw) == expected

    def test_a_hostile_display_name_cannot_run_away_with_the_caption(self):
        from app.services.document_delivery import safe_display_name

        assert len(safe_display_name("A" * 500)) <= 64


# ── filenames ───────────────────────────────────────────────────────────────

class TestFilenames:
    async def test_a_contract_is_named_for_its_contract_number(self, db_sessions):
        from app.api.downloads import _render

        async with db_sessions() as sess:
            _t, _o, inst, _p = await _shop(sess)
            _data, name, mime = await _render(kind="contract", obj_id=inst.id, db=sess)

        assert name == "MDD-2026-000001-shartnoma.pdf"
        assert str(inst.id) not in name
        assert mime == "application/pdf"

    async def test_a_receipt_is_named_for_its_contract_too(self, db_sessions):
        from app.api.downloads import _render

        async with db_sessions() as sess:
            _t, _o, _i, pay = await _shop(sess)
            _data, name, _mime = await _render(kind="payment_receipt", obj_id=pay.id, db=sess)

        assert name == "MDD-2026-000001-chek.pdf"
        assert str(pay.id) not in name


# ── idempotency and concurrency ─────────────────────────────────────────────

class TestDeliveryIsIdempotent:
    async def test_a_second_request_does_not_send_a_second_document(self, db_sessions):
        from app.services.document_delivery import DeliveryState, deliver_to_user

        sends: list[str] = []

        async def _fake_send(**kw):
            sends.append(kw["filename"])
            return DeliveryState.sent

        import app.services.document_delivery as dd
        original, dd._send = dd._send, _fake_send
        try:
            async with db_sessions() as sess:
                t, owner, inst, _p = await _shop(sess)

                async def render():
                    return b"%PDF-1.4 contract bytes"

                first = await deliver_to_user(
                    sess, user=owner, kind="contract", object_id=inst.id,
                    tenant_id=t.id, render=render,
                )
                second = await deliver_to_user(
                    sess, user=owner, kind="contract", object_id=inst.id,
                    tenant_id=t.id, render=render,
                )
        finally:
            dd._send = original

        assert first.state is DeliveryState.sent
        assert second.state is DeliveryState.already_sent
        assert len(sends) == 1, "a second document went out"

    async def test_a_concurrent_request_yields_at_most_one_delivery(self, db_sessions):
        """A REAL race, through two committed sessions.

        Both read "nothing sent yet" — which is exactly the situation the unique
        index exists for. Application code cannot arbitrate this; only the
        database can.
        """
        from app.services.document_delivery import DeliveryState, deliver_to_user

        sends: list[str] = []

        async def _fake_send(**kw):
            sends.append(kw["filename"])
            return DeliveryState.sent

        import app.services.document_delivery as dd
        original, dd._send = dd._send, _fake_send
        try:
            async with db_sessions() as setup:
                t, owner, inst, _p = await _shop(setup)
                await setup.commit()
                tid, oid, iid = t.id, owner.id, inst.id

            async def render():
                return b"%PDF-1.4 contract bytes"

            results = []
            async with db_sessions() as a, db_sessions() as b:
                ua = await a.get(User, oid)
                ub = await b.get(User, oid)
                # A inserts its claim and commits; B then tries the same claim.
                ra = await deliver_to_user(
                    a, user=ua, kind="contract", object_id=iid,
                    tenant_id=tid, render=render,
                )
                await a.commit()
                rb = await deliver_to_user(
                    b, user=ub, kind="contract", object_id=iid,
                    tenant_id=tid, render=render,
                )
                await b.rollback()
                results = [ra.state, rb.state]
        finally:
            dd._send = original

        assert results[0] is DeliveryState.sent
        assert results[1] in (DeliveryState.already_sent, DeliveryState.in_progress)
        assert len(sends) == 1, f"{len(sends)} documents went out"

    async def test_a_failed_send_may_be_retried(self, db_sessions):
        # The idempotency index excludes `failed` on purpose: a Telegram outage
        # must not permanently lock somebody out of their own contract.
        import app.services.document_delivery as dd
        from app.services.document_delivery import DeliveryState, deliver_to_user
        outcomes = iter([DeliveryState.failed, DeliveryState.sent])

        async def _fake_send(**_kw):
            return next(outcomes)

        original, dd._send = dd._send, _fake_send
        try:
            async with db_sessions() as sess:
                t, owner, inst, _p = await _shop(sess)

                async def render():
                    return b"%PDF-1.4"

                first = await deliver_to_user(
                    sess, user=owner, kind="contract", object_id=inst.id,
                    tenant_id=t.id, render=render,
                )
                second = await deliver_to_user(
                    sess, user=owner, kind="contract", object_id=inst.id,
                    tenant_id=t.id, render=render,
                )
        finally:
            dd._send = original

        assert first.state is DeliveryState.failed
        assert second.state is DeliveryState.sent

    async def test_somebody_with_no_telegram_account_gets_a_clear_state(self, db_sessions):
        from app.services.document_delivery import DeliveryState, deliver_to_user

        async with db_sessions() as sess:
            t, _o, inst, _p = await _shop(sess)
            # NOT persisted: `users.telegram_id` is NOT NULL, so this state can
            # only arise transiently — a user object built from a session that
            # has no chat id yet. The guard must still hold.
            stranger = User(id=uuid.uuid4(), telegram_id=None)

            async def render():           # pragma: no cover — must not be called
                raise AssertionError("rendered for a user with no chat")

            out = await deliver_to_user(
                sess, user=stranger, kind="contract", object_id=inst.id,
                tenant_id=t.id, render=render,
            )
        assert out.state is DeliveryState.unavailable

    async def test_the_rate_limit_is_bounded_per_user(self, db_sessions):
        import app.services.document_delivery as dd
        from app.services.document_delivery import (
            RATE_LIMIT_MAX,
            DeliveryState,
            deliver_to_user,
        )

        async def _fake_send(**_kw):
            return DeliveryState.sent

        original, dd._send = dd._send, _fake_send
        try:
            async with db_sessions() as sess:
                t, owner, _i, _p = await _shop(sess)
                # Fill the window with prior deliveries of distinct documents.
                for _ in range(RATE_LIMIT_MAX):
                    sess.add(DocumentDelivery(
                        purpose="user_delivery", kind="contract",
                        object_id=uuid.uuid4(), document_version="v",
                        user_id=owner.id, tenant_id=t.id, status="sent",
                    ))
                await sess.flush()

                async def render():       # pragma: no cover — must not be called
                    raise AssertionError("rendered while rate limited")

                out = await deliver_to_user(
                    sess, user=owner, kind="contract", object_id=uuid.uuid4(),
                    tenant_id=t.id, render=render,
                )
        finally:
            dd._send = original
        assert out.state is DeliveryState.rate_limited


class TestWhatIsActuallySent:
    """The captions above test the BUILDERS. These test what leaves the door.

    A caption function that produces the right text buys nothing if the
    delivery path passes something else — and the original defect was exactly
    that: the filename was handed over as the caption.
    """

    async def test_the_delivered_caption_is_the_built_caption(self, db_sessions):
        import app.services.document_delivery as dd
        from app.services.document_delivery import contract_caption, deliver_to_user
        seen: dict[str, str] = {}

        async def _fake_send(**kw):
            seen.update(caption=kw["caption"], filename=kw["filename"])
            return dd.DeliveryState.sent

        original, dd._send = dd._send, _fake_send
        try:
            async with db_sessions() as sess:
                t, owner, inst, _p = await _shop(sess)
                expected = await contract_caption(sess, inst.id, locale="uz")

                async def render():
                    return b"%PDF-1.4 bytes"

                await deliver_to_user(
                    sess, user=owner, kind="contract", object_id=inst.id,
                    tenant_id=t.id, render=render,
                )
        finally:
            dd._send = original

        assert seen["caption"] == expected
        # The two are DIFFERENT things and must not be confused for each other.
        assert seen["caption"] != seen["filename"]
        assert ".pdf" not in seen["caption"]
        assert "\n" in seen["caption"], "a caption is several lines, a filename is one"

    async def test_the_delivered_filename_is_human_readable(self, db_sessions):
        import app.services.document_delivery as dd
        from app.services.document_delivery import deliver_to_user
        seen: dict[str, str] = {}

        async def _fake_send(**kw):
            seen.update(filename=kw["filename"])
            return dd.DeliveryState.sent

        original, dd._send = dd._send, _fake_send
        try:
            async with db_sessions() as sess:
                t, owner, _i, pay = await _shop(sess)

                async def render():
                    return b"%PDF-1.4 bytes"

                await deliver_to_user(
                    sess, user=owner, kind="payment_receipt", object_id=pay.id,
                    tenant_id=t.id, render=render,
                )
        finally:
            dd._send = original

        assert seen["filename"] == "MDD-2026-000001-chek.pdf"
        assert str(pay.id) not in seen["filename"]


class TestFileIdCache:
    def test_a_changed_document_is_a_different_cache_key(self):
        from app.services.document_delivery import content_version

        assert content_version(b"a") != content_version(b"b")
        assert content_version(b"a") == content_version(b"a")

    async def test_a_file_id_from_another_bot_is_not_reused(self, db_sessions):
        """`file_id` is bot-scoped. Reusing one across bots returns "wrong file
        identifier", which reads like corruption rather than a config mistake —
        so `bot_owner` is part of the key rather than an assumption."""
        from app.services.document_delivery import content_version, find_archived

        data = b"%PDF-1.4 archived"
        async with db_sessions() as sess:
            t, _o, inst, _p = await _shop(sess)
            sess.add(DocumentDelivery(
                purpose="archive", kind="contract", object_id=inst.id,
                document_version=content_version(data), tenant_id=t.id,
                status="sent", file_id="OTHER-BOT-FILE-ID", bot_owner="other",
            ))
            await sess.flush()

            hit = await find_archived(
                sess, kind="contract", object_id=inst.id,
                version=content_version(data), bot_owner="platform",
            )
            same_bot = await find_archived(
                sess, kind="contract", object_id=inst.id,
                version=content_version(data), bot_owner="other",
            )
        assert hit is None, "a foreign bot's file_id was offered for reuse"
        assert same_bot is not None

    async def test_a_stale_version_is_not_served_from_the_cache(self, db_sessions):
        from app.services.document_delivery import content_version, find_archived

        async with db_sessions() as sess:
            t, _o, inst, _p = await _shop(sess)
            sess.add(DocumentDelivery(
                purpose="archive", kind="contract", object_id=inst.id,
                document_version=content_version(b"old terms"), tenant_id=t.id,
                status="sent", file_id="FILE-OLD", bot_owner="platform",
            ))
            await sess.flush()
            hit = await find_archived(
                sess, kind="contract", object_id=inst.id,
                version=content_version(b"new terms"),
            )
        assert hit is None, "a superseded contract would have been resent"


class TestSendDistinguishesForbiddenFromFailed:
    """This is the actual MUDD-112 root cause: a person who has never opened a
    chat with the bot gets `TelegramForbiddenError`, not a generic send error.
    Collapsing the two into one `failed` state is what left the Mini App with
    nothing better to tell the user than an unactionable "open a browser"."""

    async def test_forbidden_on_first_send_is_unavailable_not_failed(self, monkeypatch):
        from aiogram.exceptions import TelegramForbiddenError

        import app.services.document_delivery as dd
        import app.services.notifications as notif

        class _Bot:
            async def send_document(self, *_a, **_kw):
                raise TelegramForbiddenError(
                    method=None,
                    message="Forbidden: bot can't initiate conversation with a user",
                )

        monkeypatch.setattr(notif, "get_bot", lambda: _Bot())
        state = await dd._send(
            telegram_id=42, data=b"%PDF-1.4", filename="c.pdf", caption="",
        )
        assert state is dd.DeliveryState.unavailable

    async def test_forbidden_on_cached_file_id_reuse_is_unavailable(self, monkeypatch):
        """The file_id-reuse branch is a separate `send_document` call and a
        separate `except` — it must not fall through to the fresh-upload
        branch and try again with the same doomed chat id."""
        from aiogram.exceptions import TelegramForbiddenError

        import app.services.document_delivery as dd
        import app.services.notifications as notif

        calls: list[str] = []

        class _Bot:
            async def send_document(self, *_a, document=None, **_kw):
                calls.append("file_id" if isinstance(document, str) else "bytes")
                raise TelegramForbiddenError(
                    method=None, message="Forbidden: bot was blocked by the user",
                )

        monkeypatch.setattr(notif, "get_bot", lambda: _Bot())
        state = await dd._send(
            telegram_id=42, data=b"%PDF-1.4", filename="c.pdf", caption="",
            file_id="CACHED-ID",
        )
        assert state is dd.DeliveryState.unavailable
        assert calls == ["file_id"], "a forbidden chat was retried with a fresh upload"

    async def test_a_genuine_transient_error_is_failed_not_unavailable(self, monkeypatch):
        import app.services.document_delivery as dd
        import app.services.notifications as notif

        class _Bot:
            async def send_document(self, *_a, **_kw):
                raise RuntimeError("Telegram is down")

        monkeypatch.setattr(notif, "get_bot", lambda: _Bot())
        state = await dd._send(
            telegram_id=42, data=b"%PDF-1.4", filename="c.pdf", caption="",
        )
        assert state is dd.DeliveryState.failed


# ── the review intent ───────────────────────────────────────────────────────

class TestReviewIntent:
    def test_it_fits_telegram_and_uses_telegram_s_alphabet(self):
        token = mint(PURPOSE_PAYMENT_REVIEW, uuid.uuid4())
        assert len(token) <= MAX_STARTAPP_LEN
        allowed = set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        )
        assert set(token) <= allowed, "startapp rejects characters outside its alphabet"

    def test_it_round_trips(self):
        pid = uuid.uuid4()
        purpose, got = resolve(mint(PURPOSE_PAYMENT_REVIEW, pid))
        assert purpose == PURPOSE_PAYMENT_REVIEW and got == pid

    def test_it_carries_no_secret_a_reader_could_use(self):
        # A `startapp` parameter is visible to everyone in the chat. It must be
        # a POINTER, never a credential.
        pid = uuid.uuid4()
        token = mint(PURPOSE_PAYMENT_REVIEW, pid)
        from app.config import settings
        assert (settings.secret_key or "") not in token
        # The raw id is not readable from the token as text either.
        assert str(pid) not in token

    def test_a_tampered_intent_is_refused(self):
        token = mint(PURPOSE_PAYMENT_REVIEW, uuid.uuid4())
        flipped = ("A" if token[5] != "A" else "B").join([token[:5], token[6:]])
        with pytest.raises(InvalidIntent):
            resolve(flipped)

    def test_an_expired_intent_is_refused(self):
        token = mint(PURPOSE_PAYMENT_REVIEW, uuid.uuid4(), now=int(time.time()) - 10**7)
        with pytest.raises(InvalidIntent):
            resolve(token)

    @pytest.mark.parametrize("junk", [
        "", "x", "!!!!", "a" * 100, "AAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    ])
    def test_junk_is_refused_without_distinguishing_why(self, junk):
        with pytest.raises(InvalidIntent):
            resolve(junk)

    def test_a_signature_from_a_different_key_is_refused(self):
        from app.config import settings

        token = mint(PURPOSE_PAYMENT_REVIEW, uuid.uuid4())
        original = settings.secret_key
        try:
            settings.secret_key = "a-completely-different-key-32-bytes-long!!"
            with pytest.raises(InvalidIntent):
                resolve(token)
        finally:
            settings.secret_key = original

    def test_the_same_payment_signed_for_another_purpose_does_not_verify(self):
        # Domain separation: a signature minted for one screen must not open
        # another. There is only one purpose today, so this pins the mechanism
        # rather than an existing pair.
        import app.core.review_intent as ri

        pid = uuid.uuid4()
        token = mint(PURPOSE_PAYMENT_REVIEW, pid)
        raw = ri._unb64(token)
        forged = ri._b64(b"zz" + raw[2:])
        with pytest.raises(InvalidIntent):
            resolve(forged)


class TestDeliverySemantics:
    """What the database guarantees, and what it cannot.

    The distinction matters because the batch report originally claimed the
    claim row was "committed" before the send. A savepoint flush is not an
    outer commit, and Telegram is an external side effect with no shared
    transaction — so exactly-once external delivery is not on offer.
    """

    async def test_a_corrected_document_reaches_someone_who_has_the_old_one(
        self, db_sessions,
    ):
        """The uniqueness key includes the content version on purpose: without
        it, a contract regenerated with different terms could never be sent to
        the person holding the superseded copy."""
        import app.services.document_delivery as dd
        from app.services.document_delivery import DeliveryState, deliver_to_user
        sends: list[str] = []

        async def _fake_send(**kw):
            sends.append(kw["caption"][:20])
            return DeliveryState.sent

        original, dd._send = dd._send, _fake_send
        try:
            async with db_sessions() as sess:
                t, owner, inst, _p = await _shop(sess)
                bodies = iter([b"%PDF-1.4 terms A", b"%PDF-1.4 terms B"])

                async def render():
                    return next(bodies)

                first = await deliver_to_user(
                    sess, user=owner, kind="contract", object_id=inst.id,
                    tenant_id=t.id, render=render,
                )
                second = await deliver_to_user(
                    sess, user=owner, kind="contract", object_id=inst.id,
                    tenant_id=t.id, render=render,
                )
        finally:
            dd._send = original

        assert first.state is DeliveryState.sent
        assert second.state is DeliveryState.sent, "the corrected version was suppressed"
        assert len(sends) == 2

    async def test_the_same_document_is_still_never_sent_twice(self, db_sessions):
        import app.services.document_delivery as dd
        from app.services.document_delivery import DeliveryState, deliver_to_user
        sends: list[str] = []

        async def _fake_send(**kw):
            sends.append("x")
            return DeliveryState.sent

        original, dd._send = dd._send, _fake_send
        try:
            async with db_sessions() as sess:
                t, owner, inst, _p = await _shop(sess)

                async def render():
                    return b"%PDF-1.4 identical bytes"

                await deliver_to_user(sess, user=owner, kind="contract",
                                      object_id=inst.id, tenant_id=t.id, render=render)
                again = await deliver_to_user(sess, user=owner, kind="contract",
                                              object_id=inst.id, tenant_id=t.id,
                                              render=render)
        finally:
            dd._send = original

        assert again.state is DeliveryState.already_sent
        assert len(sends) == 1

    async def test_an_in_flight_request_is_not_taken_over(self, db_sessions):
        """A slow Telegram round trip must never look abandoned — that is the
        one way a lease could itself cause the duplicate it exists to prevent."""
        from app.services.document_delivery import (
            SENDING_LEASE_SECONDS,
            DeliveryState,
            deliver_to_user,
        )

        async with db_sessions() as sess:
            t, owner, inst, _p = await _shop(sess)
            sess.add(DocumentDelivery(
                purpose="user_delivery", kind="contract", object_id=inst.id,
                document_version=__import__("app.services.document_delivery",
                                            fromlist=["content_version"]
                                            ).content_version(b"body"),
                user_id=owner.id, tenant_id=t.id, status="sending",
            ))
            await sess.flush()

            async def render():
                return b"body"

            out = await deliver_to_user(sess, user=owner, kind="contract",
                                        object_id=inst.id, tenant_id=t.id,
                                        render=render)
        assert out.state is DeliveryState.in_progress
        assert SENDING_LEASE_SECONDS >= 300, "the lease must exceed a slow send"

    async def test_an_abandoned_sending_row_is_recoverable(self, db_sessions):
        """A process that died between claiming and sending must not lock the
        document forever."""
        from datetime import UTC, datetime, timedelta

        import app.services.document_delivery as dd
        from app.services.document_delivery import (
            SENDING_LEASE_SECONDS,
            DeliveryState,
            content_version,
            deliver_to_user,
        )

        async def _fake_send(**_kw):
            return DeliveryState.sent

        original, dd._send = dd._send, _fake_send
        try:
            async with db_sessions() as sess:
                t, owner, inst, _p = await _shop(sess)
                stale = DocumentDelivery(
                    purpose="user_delivery", kind="contract", object_id=inst.id,
                    document_version=content_version(b"body"),
                    user_id=owner.id, tenant_id=t.id, status="sending",
                )
                sess.add(stale)
                await sess.flush()
                stale.created_at = datetime.now(UTC) - timedelta(
                    seconds=SENDING_LEASE_SECONDS + 60,
                )
                await sess.flush()

                async def render():
                    return b"body"

                out = await deliver_to_user(sess, user=owner, kind="contract",
                                            object_id=inst.id, tenant_id=t.id,
                                            render=render)
        finally:
            dd._send = original
        assert out.state is DeliveryState.sent

    async def test_a_failed_archive_does_not_poison_the_cache(self, db_sessions):
        """The archive claims its row before uploading, so two concurrent
        archives cannot both upload — but a failed upload must leave a row the
        cache ignores rather than one it trusts."""
        from app.services.document_delivery import content_version, find_archived

        async with db_sessions() as sess:
            t, _o, inst, _p = await _shop(sess)
            sess.add(DocumentDelivery(
                purpose="archive", kind="contract", object_id=inst.id,
                document_version=content_version(b"x"), tenant_id=t.id,
                status="sending",          # claimed, upload not finished
            ))
            await sess.flush()
            hit = await find_archived(sess, kind="contract", object_id=inst.id,
                                      version=content_version(b"x"))
        assert hit is None, "an unfinished archive row was offered as a cache hit"
