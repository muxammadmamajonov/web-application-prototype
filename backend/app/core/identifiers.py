"""What a product identifier on a contract line means, and what it may contain.

One free-text box previously accepted anything under the label "IMEI / contract
number". Those are two different things with two different rules, and conflating
them means neither can be checked: a mistyped IMEI is indistinguishable from a
shop's own reference, and a duplicate warning cannot say what it compared.

The line therefore states WHICH kind of identifier it carries:

    IMEI                — a device identity: 15 digits, Luhn-checked
    SERIAL_OR_CONTRACT  — a shop's own serial or paper contract reference
    LEGACY_UNVERIFIED   — recorded before this distinction existed

`LEGACY_UNVERIFIED` is never assigned to new input and never re-interpreted.
A stored value that happens to be 15 digits is not evidence it is an IMEI —
guessing would attach a device-identity claim to a row nobody validated.

Both ends enforce the same rules through this module: the API imports it
directly, and `frontend/src/lib/identifiers.ts` mirrors it under a test that
compares the two definitions.
"""
from __future__ import annotations

import re
import unicodedata
from enum import StrEnum


class IdentifierType(StrEnum):
    imei = "IMEI"
    serial_or_contract = "SERIAL_OR_CONTRACT"
    legacy_unverified = "LEGACY_UNVERIFIED"


#: What a person may choose. `LEGACY_UNVERIFIED` is assigned by migration only.
SELECTABLE_TYPES: tuple[IdentifierType, ...] = (
    IdentifierType.imei,
    IdentifierType.serial_or_contract,
)

#: Agrees with `InstallmentItem.serial_number` (VARCHAR 64) and with the API
#: field. A limit the database does not share is a 500 waiting to happen.
MAX_IDENTIFIER_LENGTH = 64

IMEI_LENGTH = 15

#: Structured, translatable rejection codes. Never shown raw.
ERR_REQUIRED = "identifier_required"
ERR_IMEI_NOT_NUMERIC = "identifier_imei_not_numeric"
ERR_IMEI_LENGTH = "identifier_imei_length"
ERR_IMEI_CHECKSUM = "identifier_imei_checksum"
ERR_TOO_LONG = "identifier_too_long"
ERR_ILLEGAL_CHARS = "identifier_illegal_characters"
ERR_UNKNOWN_TYPE = "identifier_type_unknown"

#: A serial or paper reference: LETTERS in any script, decimal digits, and the
#: four separators shops actually use.
#:
#: "Letters" must not quietly mean ASCII. This product is used in Uzbek and
#: Russian, and a shop's own references look like ``ДОГОВОР-2026/15`` or
#: ``ШАРТНОМА-А12``. Uzbek Latin's ``oʻ``/``gʻ`` carry U+02BB, which Unicode
#: classifies as a modifier LETTER (Lm) — so the correct character is accepted
#: by the same rule, with no special case.
#:
#: Everything else is refused rather than escaped downstream: markup, quotes,
#: emoji, backslashes, and punctuation outside the four separators.
_SERIAL_LETTER_CATEGORIES = frozenset({"Lu", "Ll", "Lt", "Lm", "Lo", "Nd"})
_SERIAL_SEPARATORS = frozenset(" -_/")

#: Scanner output arrives with the separators printed on the label.
_IMEI_SEPARATORS = re.compile(r"[\s\-‐-―]+")

#: Invisible by category (control, format, surrogate, private-use, unassigned).
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})


def _is_variation_selector(ch: str) -> bool:
    """Variation selectors are category Mn but render as nothing.

    They ride along on pasted emoji and would otherwise be the invisible
    difference between two identifiers that look identical.
    """
    cp = ord(ch)
    return 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF


def _strip_invisibles(raw: str) -> str:
    """Remove characters that are not text but survive a copy-paste.

    Zero-width joiners, bidi overrides and control codes render as nothing and
    can make two different stored values look identical on screen — including
    to the duplicate check.
    """
    return "".join(
        ch for ch in raw
        if ch == "\t"
        or (unicodedata.category(ch) not in _INVISIBLE_CATEGORIES
            and not _is_variation_selector(ch))
    ).replace("\t", " ")


def _serial_chars_allowed(value: str) -> bool:
    return all(
        ch in _SERIAL_SEPARATORS or unicodedata.category(ch) in _SERIAL_LETTER_CATEGORIES
        for ch in value
    )


def luhn_ok(digits: str) -> bool:
    """The Luhn check digit, as GSMA specifies for an IMEI's 15th digit."""
    if not digits.isdigit() or not digits:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def normalize_imei(raw: str) -> str:
    """Digits only: a scanned label prints `35-209900-176148-1`."""
    return _IMEI_SEPARATORS.sub("", _strip_invisibles(raw)).strip()


def normalize_serial(raw: str) -> str:
    """Trim the outside and collapse internal runs of whitespace.

    Composed to NFC first, so ``Й`` typed as one codepoint and ``Й`` typed as
    two compare — and store — as the same identifier.

    Case is preserved: a serial is frequently case-significant, and upper-casing
    it would silently change what the shop wrote on paper.
    """
    composed = unicodedata.normalize("NFC", raw)
    return re.sub(r"\s+", " ", _strip_invisibles(composed)).strip()


def parse_identifier_type(raw: object) -> IdentifierType | None:
    """A declared type, or None. None is a refusal, never a default."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return IdentifierType(raw.strip().upper())
    except ValueError:
        return None


def validate_identifier(kind: object, raw: object) -> tuple[str | None, str | None]:
    """``(normalized_value, error_code)`` — exactly one of the two is set.

    An empty value with no declared type is valid and means "no identifier":
    most sales are not of serialised goods and must not be blocked.
    """
    text = raw if isinstance(raw, str) else ""
    declared = parse_identifier_type(kind)
    blank = not _strip_invisibles(text).strip()

    if declared is None:
        # No type: only an empty value is acceptable. A value without a stated
        # meaning is the ambiguity this module exists to remove.
        return (None, None) if blank else (None, ERR_UNKNOWN_TYPE)

    if declared is IdentifierType.legacy_unverified:
        # Assigned by migration to rows that predate the distinction; it can be
        # read and displayed, never submitted.
        return None, ERR_UNKNOWN_TYPE

    if blank:
        return None, ERR_REQUIRED

    if declared is IdentifierType.imei:
        value = normalize_imei(text)
        if not value.isdigit():
            return None, ERR_IMEI_NOT_NUMERIC
        if len(value) != IMEI_LENGTH:
            return None, ERR_IMEI_LENGTH
        if not luhn_ok(value):
            return None, ERR_IMEI_CHECKSUM
        return value, None

    value = normalize_serial(text)
    if not value:
        return None, ERR_REQUIRED
    if len(value) > MAX_IDENTIFIER_LENGTH:
        return None, ERR_TOO_LONG
    if not _serial_chars_allowed(value):
        return None, ERR_ILLEGAL_CHARS
    return value, None
