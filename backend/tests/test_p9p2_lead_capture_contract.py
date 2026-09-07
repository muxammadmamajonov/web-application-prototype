"""Phase 9 Pass 2 — the public lead-capture path: contract, privacy, and bounds.

Three defects found by independent review of the Pass-1 implementation:

P9-P2-001  The demo form deliberately accepts *either* an Uzbek phone number or
           an email address (`SmartContact` + `isValidPhone || isValidEmail`),
           but it posted the value as `phone` and the server typed that field
           `UzPhone`. Every email lead was rejected with a 422 — a silently
           dropped sales lead on a KEEP surface.

P9-P2-002  Both public forms put the caller's IP address and User-Agent into the
           Telegram support message. Sales follow-up does not need either, and
           the public consent copy only covers being contacted.

P9-P2-003  `esc()` truncated the *raw* field and escaped afterwards, so escaping
           expanded the result past the per-field bound — 2000 ampersands became
           10 000 characters and pushed the whole message past Telegram's 4096
           limit, which fails the send and drops the lead.

Transport is always mocked — no test here talks to Telegram.
"""
from __future__ import annotations

import re

import pytest

PHONE_LEAD = {
    "business_name": "Demo Electronics",
    "owner_name": "Sardor Mamatov",
    "contact": "+998901234567",
    "plan_code": "lite",
    "locale": "uz",
    "referrer": "https://muddato.uz/uz",
}
EMAIL_LEAD = {**PHONE_LEAD, "contact": "sardor@gmail.com"}

CONTACT_FORM = {
    "title": "Integratsiya savoli",
    "contact": "+998901234567",
    "contact_kind": "phone",
    "message": "Salom!",
    "locale": "uz",
}

#: Telegram's hard ceiling for a single `sendMessage` text.
TELEGRAM_LIMIT = 4096

#: An `&` that does not open a complete character reference — i.e. a cut that
#: landed inside an entity. Telegram rejects the whole message for one of these.
DANGLING_ENTITY = re.compile(r"&(?!(?:[a-zA-Z][a-zA-Z0-9]*|#\d+|#[xX][0-9a-fA-F]+);)")


@pytest.fixture
def sent(monkeypatch):
    """Capture support-channel deliveries instead of performing them."""
    from app.services import support_channel

    calls: list[dict] = []

    class _FakeBot:
        async def send_message(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(support_channel, "_bot", _FakeBot())
    return calls


# ── P9-P2-001. The demo lead contract accepts a phone OR an email ────────────

@pytest.mark.asyncio
async def test_a_phone_lead_is_accepted(async_client, sent):
    r = await async_client.post("/api/demo/intent", json=PHONE_LEAD)
    assert r.status_code == 202, r.text
    assert "+998901234567" in sent[0]["text"]


@pytest.mark.asyncio
async def test_an_email_lead_is_accepted(async_client, sent):
    """The regression itself: the form offers this and the server 422'd it."""
    r = await async_client.post("/api/demo/intent", json=EMAIL_LEAD)
    assert r.status_code == 202, r.text
    assert "sardor@gmail.com" in sent[0]["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "good",
    [
        "+998 90 123 45 67",     # the shape the masked form field actually yields
        "+998 33 555 44 33",
        "+998901234567",         # unmasked — a direct caller, not the form
        "998901234567",
        "901234567",             # bare local part
        "sardor@gmail.com",
        "o'tkir.aliyev@umail.uz",
    ],
)
async def test_every_value_the_form_accepts_the_api_accepts(async_client, sent, good):
    """Mirror of the corpus in `frontend/src/lib/leadContact.test.ts`.

    Nothing the browser's own gate waves through may 422 here: the visitor was
    already told the value is fine, so a rejection is a silently lost lead.
    """
    r = await async_client.post("/api/demo/intent", json={**PHONE_LEAD, "contact": good})
    assert r.status_code == 202, r.text
    assert good in sent[0]["text"], "the contact was rewritten on the way through"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "not a contact at all",
        "hello@",
        "@gmail.com",
        "sardor@gmail",          # no TLD
        "sardor gmail.com",      # whitespace
        "+9989012",              # too short for UZ
        "+1 415 555 0132",       # a real number, but not an Uzbek one
        "",
    ],
)
async def test_an_invalid_contact_is_refused(async_client, sent, bad):
    """Neither a phone nor an email must not become an accepted free-text lead."""
    r = await async_client.post("/api/demo/intent", json={**PHONE_LEAD, "contact": bad})
    assert r.status_code == 422, f"{bad!r} was accepted as a contact"
    assert sent == [], "an invalid lead still reached the support channel"


@pytest.mark.asyncio
async def test_the_legacy_phone_key_still_reaches_a_human(async_client, sent):
    """A browser holding the previous bundle posts `phone`; that lead is not lost.

    The only compatibility affordance, and it is an input alias — nothing about
    the accepted *values* is loosened by it.
    """
    legacy = {k: v for k, v in PHONE_LEAD.items() if k != "contact"}
    r = await async_client.post("/api/demo/intent", json={**legacy, "phone": "sardor@gmail.com"})
    assert r.status_code == 202, r.text
    assert "sardor@gmail.com" in sent[0]["text"]


@pytest.mark.asyncio
async def test_the_legacy_phone_key_is_validated_the_same_way(async_client, sent):
    legacy = {k: v for k, v in PHONE_LEAD.items() if k != "contact"}
    r = await async_client.post("/api/demo/intent", json={**legacy, "phone": "garbage"})
    assert r.status_code == 422
    assert sent == []


def test_the_request_model_names_the_field_for_what_it_holds():
    """`phone` was a lie about a field that may carry an email address."""
    from app.api.demo import DemoIntentRequest

    assert "contact" in DemoIntentRequest.model_fields
    assert "phone" not in DemoIntentRequest.model_fields


# ── P9-P2-002. No client metadata in a sales lead ────────────────────────────

IP = "203.0.113.77"
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [("/api/demo/intent", PHONE_LEAD), ("/api/contact/submit", CONTACT_FORM)],
)
async def test_no_client_metadata_reaches_the_support_channel(async_client, sent, path, payload):
    r = await async_client.post(
        path, json=payload, headers={"user-agent": UA, "x-forwarded-for": IP}
    )
    assert r.status_code == 202, r.text
    text = sent[0]["text"]
    assert IP not in text
    assert "AppleWebKit" not in text and "Mozilla" not in text
    # The business content the form exists to deliver is still there.
    assert payload.get("business_name", payload.get("title")) in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [("/api/demo/intent", PHONE_LEAD), ("/api/contact/submit", CONTACT_FORM)],
)
async def test_no_client_metadata_is_logged_by_this_path(async_client, sent, caplog, path, payload):
    import logging

    with caplog.at_level(logging.DEBUG):
        await async_client.post(
            path, json=payload, headers={"user-agent": UA, "x-forwarded-for": IP}
        )
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert IP not in blob
    assert "AppleWebKit" not in blob


def test_the_handlers_no_longer_read_client_metadata():
    """Structural: the plumbing is gone, not merely unused in one template."""
    import inspect

    from app.api import contact, demo

    for module in (contact, demo):
        src = inspect.getsource(module)
        assert "user-agent" not in src.lower(), f"{module.__name__} still reads the UA"
        assert "request.client" not in src, f"{module.__name__} still reads the client IP"


# ── P9-P2-003. The 4096-character bound survives escaping ────────────────────

def test_escaping_cannot_expand_a_field_past_its_bound():
    """The defect: `esc` truncated the raw value and escaped *afterwards*.

    200 ampersands survived the bound and then became 1000 characters.
    """
    from app.services.support_channel import esc

    out = esc("&" * 500, limit=200)
    assert len(out) <= 200, f"escaping expanded the field to {len(out)} characters"


@pytest.mark.parametrize(
    "hostile",
    [
        "&" * 500,
        "<" * 500,
        ">" * 500,
        "<&>" * 200,
        "Ali & Vali <b>Electronics</b> " * 40,
        "Ассалому алайкум & добро пожаловать " * 40,   # multibyte
        "🇺🇿 do'kon & savdo " * 60,                     # astral plane + combining
        "&amp;" * 200,                                  # already-escaped input
    ],
)
@pytest.mark.parametrize("limit", [1, 2, 4, 5, 6, 199, 200, 201, 2000])
def test_a_bounded_field_is_never_cut_inside_an_entity(hostile, limit):
    from app.services.support_channel import esc

    out = esc(hostile, limit=limit)
    assert len(out) <= limit or out == "—", f"{len(out)} > {limit}"
    assert DANGLING_ENTITY.search(out) is None, f"cut inside an entity: {out[-12:]!r}"


@pytest.mark.parametrize("n", [0, 1, 2, 199, 200, 201, 999, 1000, 1001])
def test_boundary_lengths_round_trip_without_corruption(n):
    """At and around the bound, whatever survives must still decode to a prefix."""
    import html

    from app.services.support_channel import esc

    raw = "a&b<c>" * n or "x"
    out = esc(raw, limit=200)
    assert DANGLING_ENTITY.search(out) is None
    assert raw.startswith(html.unescape(out)), "the surviving text is not a prefix of the input"


@pytest.mark.asyncio
async def test_a_maximal_hostile_contact_form_still_fits_in_one_telegram_message(
    async_client, sent
):
    """Every field at its model maximum, all ampersands — the 10 000-char case.

    `message` is bounded at 2000 characters; raw-truncate-then-escape turned that
    into 10 000 and pushed the whole message past 4096, so Telegram refused it and
    the lead was lost.
    """
    r = await async_client.post("/api/contact/submit", json={
        "title": "&" * 120,
        "contact": "&" * 128,
        "contact_kind": "email",
        "message": "&" * 2000,
        "locale": "ru",
    })
    assert r.status_code == 202, r.text
    text = sent[0]["text"]
    assert len(text) <= TELEGRAM_LIMIT, f"formatted message is {len(text)} characters"
    assert DANGLING_ENTITY.search(text) is None


@pytest.mark.asyncio
async def test_a_maximal_hostile_demo_lead_still_fits_in_one_telegram_message(
    async_client, sent
):
    r = await async_client.post("/api/demo/intent", json={
        "business_name": "&<>" * 40,
        "owner_name": "&<>" * 26 + "&",
        "contact": "+998901234567",
        "plan_code": "&" * 40,
        "locale": "uz",
        "referrer": "&" * 120,
    })
    assert r.status_code == 202, r.text
    text = sent[0]["text"]
    assert len(text) <= TELEGRAM_LIMIT, f"formatted message is {len(text)} characters"
    assert DANGLING_ENTITY.search(text) is None


@pytest.mark.asyncio
async def test_the_labels_the_reader_needs_survive_a_maximal_payload(async_client, sent):
    """A bound that ate the headings would leave a human staring at ampersands."""
    await async_client.post("/api/contact/submit", json={
        "title": "&" * 120,
        "contact": "&" * 128,
        "contact_kind": "phone",
        "message": "&" * 2000,
        "locale": "uz",
    })
    text = sent[0]["text"]
    for label in ("Yangi murojaat", "Sarlavha:", "Telefon:", "Til:", "Xabar:"):
        assert label in text, f"{label!r} was truncated away"


def test_the_final_message_bound_is_enforced_not_merely_arithmetic():
    """Defence in depth: a future template edit must not be able to break the bound."""
    from app.services.support_channel import bound_for_telegram

    huge = "<b>Sarlavha:</b> " + "&amp;" * 2000 + "\n<i>oxiri</i>"
    assert len(huge) > TELEGRAM_LIMIT
    out = bound_for_telegram(huge)
    assert len(out) <= TELEGRAM_LIMIT
    assert DANGLING_ENTITY.search(out) is None
    assert out.count("<b>") == out.count("</b>"), "left an unclosed tag"
    assert out.count("<i>") == out.count("</i>"), "left an unclosed tag"


def test_the_final_bound_leaves_a_conforming_message_untouched():
    from app.services.support_channel import bound_for_telegram

    ok = "<b>Yangi murojaat</b>\n\n<b>Sarlavha:</b> Ali &amp; Vali"
    assert bound_for_telegram(ok) == ok


@pytest.mark.parametrize("n", [4090, 4095, 4096, 4097, 4100, 8000])
def test_the_final_bound_holds_at_and_around_the_limit(n):
    from app.services.support_channel import bound_for_telegram

    out = bound_for_telegram("<b>x</b>" + "y" * n)
    assert len(out) <= TELEGRAM_LIMIT
    assert DANGLING_ENTITY.search(out) is None
    assert out.count("<b>") == out.count("</b>")


@pytest.mark.parametrize("pad", [0, 1, 2, 3, 4, 5, 6, 7])
def test_the_final_bound_never_splits_a_tag(pad):
    """Walk a tag across the ceiling so the cut lands *inside* it, not beside it.

    Sweeping the offset matters: a fixture that happens to cut at a tag boundary
    passes whether or not the guard exists. Only an index strictly between ``<``
    and ``>`` exercises it.
    """
    from app.services.support_channel import bound_for_telegram

    prefix = "z" * (TELEGRAM_LIMIT - 8 + pad)
    out = bound_for_telegram(prefix + "<b>tail</b>" + "z" * 100)
    assert len(out) <= TELEGRAM_LIMIT
    assert not re.search(r"<[^>]*$", out), f"message ends inside a tag: {out[-12:]!r}"
    assert out.count("<b>") == out.count("</b>"), "left an unclosed tag"


@pytest.mark.asyncio
async def test_the_send_path_applies_the_bound_not_just_the_helper(sent):
    """The guarantee has to be wired in, not merely available.

    Asserted at `send_to_support` rather than on `bound_for_telegram` alone: a
    bound the delivery path does not call is not a bound.
    """
    from app.services.support_channel import send_to_support

    await send_to_support("<b>Sarlavha:</b> " + "&amp;" * 2000 + "\n<i>oxiri</i>")
    text = sent[0]["text"]
    assert len(text) <= TELEGRAM_LIMIT, f"{len(text)} characters left the send path"
    assert DANGLING_ENTITY.search(text) is None
    assert text.count("<b>") == text.count("</b>")
    assert text.count("<i>") == text.count("</i>")


@pytest.mark.asyncio
async def test_the_send_path_leaves_an_ordinary_lead_untouched(sent):
    from app.services.support_channel import send_to_support

    ok = "<b>Yangi murojaat</b>\n\n<b>Sarlavha:</b> Ali &amp; Vali"
    await send_to_support(ok)
    assert sent[0]["text"] == ok
    assert sent[0]["parse_mode"] == "HTML"
