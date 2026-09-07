"""A customer who already uses the bot gets told, and gets a way to reply.

WHAT THIS IS FOR. The platform could only say "linked to this shop" or "not
connected to Telegram". The second lumped together two very different people:
somebody the platform has never seen, and somebody who already has an account
from another shop. The first needs an invite link handed over by some other
channel; the second is reachable right now.

The rules worth pinning are the ones that fail silently if broken:

  * A `tel:` url must never reach an inline button. Telegram rejects the WHOLE
    message for one bad button, so a shop with a contact phone but no Telegram
    handle would have lost every notification — not just the button.
  * "Open the app" must be a t.me link, not the web app. An https link drops
    the person into a browser where they are not signed in, which is the worst
    possible landing for someone who just read "you have a new purchase".
  * A failed send must not fail the sale.
"""
from __future__ import annotations

import pytest

from app.services import customer_identity as ci
from app.services import customer_outreach as co
from app.services.notifications import _is_button_url

# ── inline button urls ───────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://t.me/muddatobot", "http://example.com", "tg://resolve?domain=x",
])
def test_telegram_accepts_these_button_urls(url):
    assert _is_button_url(url) is True


@pytest.mark.parametrize("url", [
    "tel:+998901112233",       # the field an operator is most likely to have filled in
    "mailto:a@b.com", "", None, "t.me/muddatobot",   # no scheme
])
def test_everything_else_is_dropped_rather_than_sent(url):
    # Dropped, NOT sent-and-hoped: Telegram refuses the entire message for one
    # bad button, turning a cosmetic problem into a lost notification.
    assert _is_button_url(url) is False


@pytest.mark.parametrize("handle,expected", [
    ("muddatoshop",  "https://t.me/muddatoshop"),
    ("@muddatoshop", "https://t.me/muddatoshop"),   # operators type the @
    ("  @shop  ",    "https://t.me/shop"),
])
def test_a_telegram_handle_becomes_a_contact_button(handle, expected):
    assert co._contact_url(handle) == expected


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_no_handle_means_no_contact_button(missing):
    # Better one button than two, when the second goes nowhere.
    assert co._contact_url(missing) is None


# ── the message ──────────────────────────────────────────────────────────────

def test_both_languages_carry_every_key():
    assert set(co._COPY["uz"]) == set(co._COPY["ru"])
    for locale in ("uz", "ru"):
        for key, value in co._COPY[locale].items():
            assert value.strip(), f"{locale}.{key} is blank"


def test_the_purchase_notice_names_the_shop_and_the_goods():
    body = co._COPY["uz"]["purchase_body"]
    assert "{shop}" in body and "{items}" in body
    assert co._COPY["ru"]["purchase_body"].count("{shop}") == 1


def test_the_notice_tells_the_reader_what_to_do_if_it_was_not_them():
    # The entire reason the second button exists.
    assert "bogʻlaning" in co._COPY["uz"]["purchase_body"]
    assert "свяжитесь" in co._COPY["ru"]["purchase_body"]


def test_an_unknown_locale_falls_back_rather_than_raising():
    assert co._copy("de") is co._COPY["uz"]
    assert co._copy(None) is co._COPY["uz"]
    assert co._copy("ru-RU") is co._COPY["ru"]


# ── the three statuses ───────────────────────────────────────────────────────

class _C:
    def __init__(self, telegram_id=None, phone=None):
        self.telegram_id = telegram_id
        self.phone = phone


def test_a_linked_customer_is_joined():
    assert ci.status_for(_C(telegram_id=42, phone="+998901112233"), {}) == ci.JOINED


def test_an_unlinked_customer_the_platform_knows_is_known():
    known = {"998901112233": 7}
    assert ci.status_for(_C(phone="+998 90 111 22 33"), known) == ci.KNOWN


def test_a_customer_nobody_has_seen_is_new():
    assert ci.status_for(_C(phone="+998900000000"), {"998901112233": 7}) == ci.NEW


def test_the_lookup_key_ignores_formatting():
    # Same person however the operator typed it — the point of canonicalisation.
    assert ci._key("+998 90 111 22 33") == ci._key("998901112233") == ci._key("901112233")


def test_a_number_that_is_not_a_uz_mobile_has_no_key():
    for junk in (None, "", "+1 555 0100", "nope"):
        assert ci._key(junk) is None


def test_a_customer_with_a_junk_phone_is_new_not_crashing():
    assert ci.status_for(_C(phone="nonsense"), {"998901112233": 7}) == ci.NEW
