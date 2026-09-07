"""#14 — Business Settings: the name is never blank, the logo URL is never junk,
and withdrawing a payment method does not destroy anyone's configuration.

The last one is the point worth stating. "Temporarily remove Payme, Click and
Bank Transfer" is easy to implement destructively — clear the flags, drop the
credentials — and the damage only shows up when the methods come back and every
tenant has to reconfigure from memory. So these tests assert the opposite of the
obvious implementation: that stored settings survive being withdrawn from the UI
untouched, and that only the READ is filtered.
"""
from __future__ import annotations

import pytest

from app.core import payment_methods as pm
from app.core.validators import LOGO_URL_MAX, LOGO_URL_MIN, validate_logo_url

# ── logo URL ─────────────────────────────────────────────────────────────────

def test_the_field_stays_optional():
    for blank in (None, "", "   "):
        assert validate_logo_url(blank) is None


def test_a_normal_https_url_is_accepted():
    url = "https://cdn.example.com/logos/shop.png"
    assert validate_logo_url(url) == url


def test_surrounding_whitespace_is_stripped_not_rejected():
    # Pasting a URL commonly brings a trailing space; rejecting that would be
    # a validation error the owner cannot see.
    assert validate_logo_url("  https://cdn.example.com/a.png  ") == "https://cdn.example.com/a.png"


@pytest.mark.parametrize("url,reason", [
    ("http://cdn.example.com/a.png",  "logo_url_bad_scheme"),
    ("javascript:alert(1)",           "logo_url_bad_scheme"),
    ("data:image/png;base64,AAAA",    "logo_url_bad_scheme"),
    ("file:///etc/passwd",            "logo_url_bad_scheme"),
    ("https://localhost/a.png",       "logo_url_malformed"),   # no dotted host
    ("not a url at all",              "logo_url_bad_scheme"),
])
def test_unusable_values_are_named_not_stored(url, reason):
    with pytest.raises(ValueError) as e:
        validate_logo_url(url)
    assert str(e.value) == reason


def test_the_length_bounds_are_enforced_at_both_ends():
    assert len("https://a") < LOGO_URL_MIN                 # the fixture is genuinely short
    with pytest.raises(ValueError, match="too_short"):
        validate_logo_url("https://a")
    with pytest.raises(ValueError, match="too_long"):
        validate_logo_url("https://e.com/" + "a" * LOGO_URL_MAX)


def test_http_is_refused_because_the_browser_would_refuse_it_anyway():
    # Not pedantry: the app is HTTPS, so an http logo is blocked as mixed
    # content and renders as nothing. Storing it would hide that from the owner.
    with pytest.raises(ValueError, match="bad_scheme"):
        validate_logo_url("http://cdn.example.com/logo.png")


# ── which payment methods are offered ────────────────────────────────────────

def test_only_cash_and_card_are_offered_today():
    assert pm.OFFERED == ("cash", "card")


def test_the_withdrawn_methods_are_still_part_of_the_vocabulary():
    # They must stay in ALL_METHODS: stored settings reference them, and a
    # tenant's saved Click credentials are meaningless if the key is unknown.
    for m in ("transfer", "click", "payme"):
        assert m in pm.ALL_METHODS
        assert not pm.is_offered(m)
    assert set(pm.WITHDRAWN) == {"transfer", "click", "payme"}


def test_withdrawing_a_method_never_rewrites_stored_settings():
    # The module is read-only by construction — it exposes no function that
    # takes a session. If that ever changes, restoring a method stops being
    # free, so the absence is asserted rather than assumed.
    import inspect
    src = inspect.getsource(pm)
    for forbidden in ("db.", "session", "update(", "delete("):
        assert forbidden not in src, f"payment_methods must not write: found {forbidden!r}"


# ── card availability ────────────────────────────────────────────────────────

def test_card_is_not_usable_without_a_receiving_card():
    assert pm.card_is_usable({"card": True, "cards": []}) is False
    assert pm.card_is_usable({"card": True}) is False
    assert pm.card_is_usable({"card": True, "cards": None}) is False


def test_card_is_usable_once_a_card_exists():
    assert pm.card_is_usable({"card": True, "cards": [{"id": "a", "number": "8600000000000000"}]}) is True


def test_card_off_is_off_even_with_cards_saved():
    assert pm.card_is_usable({"card": False, "cards": [{"id": "a"}]}) is False


def test_missing_settings_do_not_crash_the_check():
    assert pm.card_is_usable(None) is False
    assert pm.card_is_usable({}) is False
