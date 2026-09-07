"""FB-006 — a product identifier states what it is, and is checked accordingly.

THE DEFECT (founder beta, 2026-08-09). One box labelled "IMEI / Shartnoma
raqami" took unlimited arbitrary text. IMEI and a shop's own paper reference do
not share a single rule, so neither was enforced: a mistyped IMEI stored fine, a
pasted string with invisible formatting characters compared unequal to the same
string typed by hand, and the duplicate warning could not say what it had
matched.

The line now declares WHICH identifier it carries, and each kind is validated
under its own rule — on both ends, from this one module.

`LEGACY_UNVERIFIED` is the third value, assigned by migration to rows written
before the distinction existed. It is deliberately not guessable from content:
these tests pin that a 15-digit legacy string is NOT promoted to IMEI.
"""
from __future__ import annotations

import pytest

from app.core.identifiers import (
    ERR_ILLEGAL_CHARS,
    ERR_IMEI_CHECKSUM,
    ERR_IMEI_LENGTH,
    ERR_IMEI_NOT_NUMERIC,
    ERR_REQUIRED,
    ERR_TOO_LONG,
    ERR_UNKNOWN_TYPE,
    MAX_IDENTIFIER_LENGTH,
    SELECTABLE_TYPES,
    IdentifierType,
    luhn_ok,
    normalize_imei,
    normalize_serial,
    parse_identifier_type,
    validate_identifier,
)

#: Real-shaped IMEIs with a correct Luhn check digit.
VALID_IMEI = "352099001761481"
VALID_IMEI_2 = "490154203237518"


# ── the checksum ────────────────────────────────────────────────────────────

def test_luhn_accepts_a_real_imei() -> None:
    assert luhn_ok(VALID_IMEI) is True
    assert luhn_ok(VALID_IMEI_2) is True


def test_luhn_rejects_every_transposition_it_is_capable_of_catching() -> None:
    """The error the check digit exists for — with its one known blind spot.

    Luhn catches all adjacent transpositions except `09` ↔ `90`, which leave the
    sum unchanged. Asserting a hand-picked pair would silently pass if the
    checksum stopped working, so every adjacent pair is tried and the exemption
    is stated rather than avoided.
    """
    caught = 0
    for i in range(len(VALID_IMEI) - 1):
        a, b = VALID_IMEI[i], VALID_IMEI[i + 1]
        if a == b:
            continue
        swapped = VALID_IMEI[:i] + b + a + VALID_IMEI[i + 2:]
        if {a, b} == {"0", "9"}:
            assert luhn_ok(swapped) is True, f"{i}: the documented blind spot moved"
            continue
        assert luhn_ok(swapped) is False, f"{i}: {swapped}"
        caught += 1
    assert caught >= 5, caught


def test_luhn_rejects_non_digits_and_empty() -> None:
    assert luhn_ok("") is False
    assert luhn_ok("35209900176148A") is False


# ── IMEI ────────────────────────────────────────────────────────────────────

def test_a_valid_imei_is_accepted_and_stored_as_digits() -> None:
    value, err = validate_identifier("IMEI", VALID_IMEI)
    assert err is None
    assert value == VALID_IMEI


@pytest.mark.parametrize("printed", [
    "35-209900-176148-1",      # the separators printed on a device label
    "35 209900 176148 1",
    " 352099001761481 ",
    "352099001761481​",   # a zero-width space from a copy-paste
])
def test_scanner_and_label_formatting_is_normalised_before_validation(printed) -> None:
    value, err = validate_identifier("IMEI", printed)
    assert err is None, printed
    assert value == VALID_IMEI


def test_an_imei_with_letters_is_refused() -> None:
    assert validate_identifier("IMEI", "35209900176148A")[1] == ERR_IMEI_NOT_NUMERIC


@pytest.mark.parametrize("wrong", ["35209900176148", "3520990017614812", "1"])
def test_an_imei_of_the_wrong_length_is_refused(wrong) -> None:
    assert validate_identifier("IMEI", wrong)[1] == ERR_IMEI_LENGTH


def test_fifteen_digits_that_fail_the_check_digit_are_refused() -> None:
    bad = VALID_IMEI[:14] + str((int(VALID_IMEI[14]) + 1) % 10)
    assert validate_identifier("IMEI", bad)[1] == ERR_IMEI_CHECKSUM


def test_an_empty_imei_is_refused_once_the_type_is_declared() -> None:
    assert validate_identifier("IMEI", "")[1] == ERR_REQUIRED
    assert validate_identifier("IMEI", "   ")[1] == ERR_REQUIRED


# ── serial / contract reference ─────────────────────────────────────────────

@pytest.mark.parametrize("ok", [
    "SN-2026/0142", "abc_123", "Kassa 17/B", "A", "x" * MAX_IDENTIFIER_LENGTH,
])
def test_a_reasonable_serial_is_accepted(ok) -> None:
    value, err = validate_identifier("SERIAL_OR_CONTRACT", ok)
    assert err is None, ok
    assert value == ok


def test_a_serial_keeps_its_case() -> None:
    """Serials are frequently case-significant; upper-casing rewrites them."""
    value, _ = validate_identifier("SERIAL_OR_CONTRACT", "SnAbC-9")
    assert value == "SnAbC-9"


def test_outer_whitespace_is_trimmed_and_inner_runs_collapsed() -> None:
    value, err = validate_identifier("SERIAL_OR_CONTRACT", "  SN 2026   0142  ")
    assert err is None
    assert value == "SN 2026 0142"


def test_a_serial_longer_than_the_column_is_refused() -> None:
    err = validate_identifier("SERIAL_OR_CONTRACT", "x" * (MAX_IDENTIFIER_LENGTH + 1))[1]
    assert err == ERR_TOO_LONG


@pytest.mark.parametrize("bad", [
    "<script>alert(1)</script>", "SN;DROP", "a,b", "quote\"here", "emoji 🙂",
    "back\\slash", "semi:colon", "plus+plus",
])
def test_markup_and_stray_punctuation_are_refused(bad) -> None:
    assert validate_identifier("SERIAL_OR_CONTRACT", bad)[1] == ERR_ILLEGAL_CHARS


@pytest.mark.parametrize("invisible", [
    "SN​0142",      # zero-width space
    "SN‎0142",      # left-to-right mark
    "SN‮0142",      # right-to-left override
    "SN\x000142",        # NUL
])
def test_invisible_formatting_characters_are_stripped_not_stored(invisible) -> None:
    """Two values that look identical must not compare unequal."""
    value, err = validate_identifier("SERIAL_OR_CONTRACT", invisible)
    assert err is None, invisible
    assert value == "SN0142"


def test_a_serial_that_is_only_invisibles_is_refused() -> None:
    assert validate_identifier("SERIAL_OR_CONTRACT", "​​")[1] == ERR_REQUIRED


# ── the type itself ─────────────────────────────────────────────────────────

def test_no_identifier_at_all_is_valid() -> None:
    """Most sales are not of serialised goods; this must not block them."""
    assert validate_identifier(None, None) == (None, None)
    assert validate_identifier(None, "") == (None, None)
    assert validate_identifier("", "  ") == (None, None)


def test_a_value_with_no_declared_type_is_refused() -> None:
    """The ambiguity this whole change removes."""
    assert validate_identifier(None, "352099001761481")[1] == ERR_UNKNOWN_TYPE
    assert validate_identifier("", "SN-1")[1] == ERR_UNKNOWN_TYPE


def test_an_unrecognised_type_is_refused() -> None:
    assert validate_identifier("PASSPORT", "SN-1")[1] == ERR_UNKNOWN_TYPE
    assert parse_identifier_type("PASSPORT") is None


def test_the_legacy_class_can_never_be_submitted() -> None:
    """It is a migration outcome, not an input a client may choose."""
    assert IdentifierType.legacy_unverified not in SELECTABLE_TYPES
    assert validate_identifier("LEGACY_UNVERIFIED", VALID_IMEI)[1] == ERR_UNKNOWN_TYPE


def test_fifteen_digits_are_never_promoted_to_imei_by_content() -> None:
    """A legacy string that looks like an IMEI is still not an IMEI claim."""
    value, err = validate_identifier("SERIAL_OR_CONTRACT", VALID_IMEI)
    assert err is None
    assert value == VALID_IMEI          # stored as what it was declared to be
    assert parse_identifier_type(VALID_IMEI) is None


@pytest.mark.parametrize("case", ["imei", "Imei", " imei ", "serial_or_contract"])
def test_type_case_and_padding_are_tolerated(case) -> None:
    assert parse_identifier_type(case) is not None


# ── the two ends cannot drift ───────────────────────────────────────────────

def test_the_length_limit_matches_the_database_column() -> None:
    from app.models import InstallmentItem

    assert InstallmentItem.__table__.c.serial_number.type.length == MAX_IDENTIFIER_LENGTH


def test_the_api_field_limit_matches_too() -> None:
    from app.api.tenant.installments import InstallmentItemIn

    field = InstallmentItemIn.model_fields["serial_number"]
    limits = [m for m in field.metadata if getattr(m, "max_length", None)]
    assert limits and limits[0].max_length == MAX_IDENTIFIER_LENGTH


def test_the_frontend_mirror_declares_the_same_rules() -> None:
    """A second copy of the rules that can disagree is worse than none."""
    import pathlib

    ts = (pathlib.Path(__file__).resolve().parents[2]
          / "frontend/src/lib/identifiers.ts").read_text()
    for token in (*(t.value for t in IdentifierType), str(MAX_IDENTIFIER_LENGTH),
                  ERR_IMEI_CHECKSUM, ERR_IMEI_LENGTH, ERR_IMEI_NOT_NUMERIC,
                  ERR_ILLEGAL_CHARS, ERR_TOO_LONG, ERR_REQUIRED):
        assert token in ts, token


def test_the_frontend_mirror_is_not_ascii_only() -> None:
    """The regression this suite exists to prevent, pinned in the mirror too.

    An `[A-Za-z0-9 ...]` class silently means "Latin shops only" — it rejects
    every Cyrillic contract number the product's own users write.
    """
    import pathlib

    ts = (pathlib.Path(__file__).resolve().parents[2]
          / "frontend/src/lib/identifiers.ts").read_text()
    assert "A-Za-z0-9 _/-]+$/" not in ts, "the ASCII-only serial pattern is back"
    assert r"\p{L}" in ts and r"\p{Nd}" in ts
    assert "normalize('NFC')" in ts


# ── letters are not ASCII ───────────────────────────────────────────────────

@pytest.mark.parametrize("ok", [
    "ДОГОВОР-2026/15",      # Russian, the exact form the founder writes
    "ШАРТНОМА-А12",         # Uzbek Cyrillic
    "СЕРИЯ_Б-44",
    "договор 17",           # lowercase Cyrillic
    "SHARTNOMA-Oʻ12",       # Uzbek Latin: U+02BB is a modifier LETTER
    "GʻALLA 2026/7",
    "ABC-123/Д_44",         # mixed script
])
def test_letters_in_any_script_are_accepted(ok) -> None:
    value, err = validate_identifier("SERIAL_OR_CONTRACT", ok)
    assert err is None, ok
    assert value == ok


def test_cyrillic_keeps_its_case_too() -> None:
    value, _ = validate_identifier("SERIAL_OR_CONTRACT", "Договор-Аб")
    assert value == "Договор-Аб"


def test_the_length_limit_counts_characters_not_bytes() -> None:
    """Cyrillic is two bytes per letter; the column counts characters."""
    ok, err = validate_identifier("SERIAL_OR_CONTRACT", "Д" * MAX_IDENTIFIER_LENGTH)
    assert err is None
    assert ok == "Д" * MAX_IDENTIFIER_LENGTH
    over = validate_identifier("SERIAL_OR_CONTRACT", "Д" * (MAX_IDENTIFIER_LENGTH + 1))
    assert over[1] == ERR_TOO_LONG


def test_decomposed_letters_compose_before_storage() -> None:
    """`Й` typed as two codepoints must not be a different identifier."""
    composed, err = validate_identifier("SERIAL_OR_CONTRACT", "Й-7")
    decomposed, err2 = validate_identifier("SERIAL_OR_CONTRACT", "Й-7")
    assert err is None and err2 is None
    assert composed == decomposed == "Й-7"


@pytest.mark.parametrize("bad", [
    "ДОГОВОР😀", "ДОГОВОР‭<b>", "Договор’15", "Договор«15»", "Договор№15",
])
def test_widening_to_unicode_did_not_admit_symbols(bad) -> None:
    """Only LETTERS and DIGITS widened. Emoji, markup and quotes stay out."""
    assert validate_identifier("SERIAL_OR_CONTRACT", bad)[1] == ERR_ILLEGAL_CHARS


def test_variation_selectors_are_invisible_and_removed() -> None:
    value, err = validate_identifier("SERIAL_OR_CONTRACT", "SN️1")
    assert err is None
    assert value == "SN1"


@pytest.mark.parametrize("cyrillic_imei", ["35209900176148Д", "З52099001761481"])
def test_imei_was_not_widened(cyrillic_imei) -> None:
    """Requirement 10: only the serial class learned about Unicode."""
    assert validate_identifier("IMEI", cyrillic_imei)[1] == ERR_IMEI_NOT_NUMERIC


# ── the shared vector contract ──────────────────────────────────────────────

def _vectors() -> list[dict]:
    import json
    import pathlib

    path = (pathlib.Path(__file__).resolve().parents[2]
            / "frontend/src/lib/identifierVectors.json")
    return json.loads(path.read_text())["cases"]


@pytest.mark.parametrize("vector", _vectors(), ids=lambda v: v["name"])
def test_shared_vectors_hold_on_the_server(vector) -> None:
    """Every case here is run byte-for-byte by the client suite as well.

    The file is the contract. A rule only one side enforces is a round-trip
    that fails after the shop has already typed the value.
    """
    value, error = validate_identifier(vector["type"], vector["input"])
    assert value == vector["value"], vector["name"]
    assert error == vector["error"], vector["name"]


def test_the_vector_file_covers_every_mandated_class() -> None:
    """A shared fixture nobody extends stops being a contract."""
    names = " ".join(v["name"] for v in _vectors())
    for required in ("latin", "uzbek", "cyrillic", "mixed", "emoji", "markup",
                     "invisible", "64", "65", "imei"):
        assert required in names, required
    assert len(_vectors()) >= 40


def test_normalisers_are_idempotent() -> None:
    for raw in ("35-2099 00176148-1", "  SN  17/B  "):
        once_imei, once_serial = normalize_imei(raw), normalize_serial(raw)
        assert normalize_imei(once_imei) == once_imei
        assert normalize_serial(once_serial) == once_serial
