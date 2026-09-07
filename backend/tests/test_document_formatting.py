"""How a document writes a number, a date and an enum.

Each case here is something a founder-beta reader actually saw on a document
they were handed: `6000000.00` where six million so'm belonged, `cash` and
`approved` sitting in English inside an Uzbek receipt, a raw ISO timestamp with
microseconds, and `≈` in front of a figure that was exact.

The rule the whole module exists to enforce: a formatter renders, it never
computes. Every arithmetic assertion below is really asking "did the display
layer quietly change a financial value" — and the answer must stay no.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.core.documents import (
    _GROUP_SEP as NNBSP,
)
from app.core.documents import (
    currency_label,
    document_filename,
    long_date,
    long_datetime,
    money,
    money_with_currency,
    normalize_locale,
    payment_method_label,
    payment_status_label,
    safe_filename_part,
)

# ── money ───────────────────────────────────────────────────────────────────

class TestMoney:
    @pytest.mark.parametrize("raw,expected", [
        ("6000000.00", f"6{NNBSP}000{NNBSP}000"),      # the contract figure from the report
        ("4800000.00", f"4{NNBSP}800{NNBSP}000"),      # the receipt figure from the report
        (Decimal("0"), "0"),
        (Decimal("999"), "999"),
        (Decimal("1000"), f"1{NNBSP}000"),
        (Decimal("12345678901"), f"12{NNBSP}345{NNBSP}678{NNBSP}901"),
    ])
    def test_som_is_grouped_and_carries_no_decimals(self, raw, expected):
        # So'm has no circulating sub-unit, so ".00" is noise that also invites
        # the reader to wonder what it is hiding.
        assert money(raw, "UZS") == expected

    @pytest.mark.parametrize("raw,expected", [
        ("1234.5", f"1{NNBSP}234,50"),
        ("1234.567", f"1{NNBSP}234,57"),         # rounds half-up, for DISPLAY only
        ("0.004", "0,00"),
    ])
    def test_a_decimal_currency_keeps_two_places(self, raw, expected):
        assert money(raw, "USD") == expected

    def test_the_group_separator_cannot_be_broken_across_lines(self):
        # A plain space lets a line break fall inside a number: "4 800\n000".
        assert " " in money("4800000", "UZS")
        assert " " not in money("4800000", "UZS")

    def test_a_negative_amount_keeps_its_sign_outside_the_groups(self):
        assert money("-4800000", "UZS") == f"-4{NNBSP}800{NNBSP}000"

    def test_rounding_is_presentational_and_never_written_back(self):
        original = Decimal("1234.567")
        money(original, "USD")
        assert original == Decimal("1234.567")

    def test_nothing_renders_as_nothing(self):
        assert money(None) == ""
        assert money(None, "UZS") == ""

    def test_junk_is_shown_rather_than_crashing_a_contract(self):
        # A malformed value must not take down a whole render on somebody's
        # contract; show what we were handed.
        assert money("not-a-number", "UZS") == "not-a-number"

    def test_the_currency_label_is_localized_but_the_number_is_not(self):
        # The separator INSIDE the number is unbreakable; the one before the
        # currency word is an ordinary space, so a long line may wrap there.
        assert money_with_currency("4800000", "UZS", "uz") == f"4{NNBSP}800{NNBSP}000 so'm"
        assert money_with_currency("4800000", "UZS", "ru") == f"4{NNBSP}800{NNBSP}000 сум"

    def test_an_unknown_currency_shows_its_code_rather_than_vanishing(self):
        assert currency_label("XYZ", "uz") == "XYZ"
        assert currency_label(None) == ""


# ── dates ───────────────────────────────────────────────────────────────────

class TestDates:
    def test_the_uzbek_form_is_the_one_in_the_specification(self):
        assert long_date(date(2026, 9, 1), "uz") == "1-sentabr, 2026"

    def test_the_russian_form(self):
        assert long_date(date(2026, 9, 1), "ru") == "1 сентября 2026 г."

    def test_an_iso_string_is_accepted_because_half_the_contexts_carry_one(self):
        assert long_date("2026-09-01", "uz") == "1-sentabr, 2026"

    def test_an_unparseable_string_is_returned_rather_than_raising(self):
        assert long_date("garbage", "uz") == "garbage"

    def test_a_timestamp_loses_its_microseconds(self):
        # The exact defect: 2026-09-01T14:23:11.482913+00:00 on a receipt line
        # narrow enough that the value collided with its own label.
        stamp = datetime(2026, 9, 1, 9, 23, 11, 482913, tzinfo=UTC)
        out = long_datetime(stamp, "uz")
        assert "482913" not in out
        assert "+00:00" not in out
        assert out == "1-sentabr, 2026, 14:23"      # 09:23 UTC is 14:23 in Tashkent

    def test_utc_is_converted_to_the_business_timezone_before_formatting(self):
        # 21:00 UTC is the NEXT day in Tashkent. Formatting without converting
        # would date a payment to the wrong day.
        stamp = datetime(2026, 8, 31, 21, 0, tzinfo=UTC)
        assert long_datetime(stamp, "uz").startswith("1-sentabr, 2026")

    def test_a_naive_timestamp_is_read_as_utc_not_as_local(self):
        # What the database hands back for a column written without an offset.
        # Guessing "local" would silently shift a payment across midnight.
        naive = datetime(2026, 8, 31, 21, 0)
        assert long_datetime(naive, "uz").startswith("1-sentabr, 2026")

    def test_no_raw_iso_survives_any_supported_locale(self):
        for loc in ("uz", "ru"):
            assert "2026-09-01" not in long_date(date(2026, 9, 1), loc)


# ── enums ───────────────────────────────────────────────────────────────────

class TestEnumLabels:
    @pytest.mark.parametrize("code,uz,ru", [
        ("cash", "Naqd", "Наличные"),
        ("card", "Karta", "Карта"),
        ("transfer", "Bank o'tkazmasi", "Банковский перевод"),
        ("late_fee", "Penya", "Пеня"),
    ])
    def test_a_payment_method_is_never_left_in_english(self, code, uz, ru):
        assert payment_method_label(code, "uz") == uz
        assert payment_method_label(code, "ru") == ru

    @pytest.mark.parametrize("code,uz,ru", [
        ("pending", "Kutilmoqda", "Ожидает"),
        ("approved", "Tasdiqlangan", "Подтверждён"),
        ("rejected", "Rad etilgan", "Отклонён"),
    ])
    def test_a_payment_status_is_never_left_in_english(self, code, uz, ru):
        assert payment_status_label(code, "uz") == uz
        assert payment_status_label(code, "ru") == ru

    def test_every_enum_member_has_copy_in_every_locale(self):
        # The guard that catches a new member reaching a document before its
        # translation does.
        from app.models.enums import PaymentMethod, PaymentStatus

        for loc in ("uz", "ru"):
            for m in PaymentMethod:
                assert payment_method_label(m, loc) != m.value, f"{loc}/{m.value} untranslated"
            for s in PaymentStatus:
                assert payment_status_label(s, loc) != s.value, f"{loc}/{s.value} untranslated"

    def test_the_enum_object_itself_is_accepted_not_only_its_value(self):
        from app.models.enums import PaymentMethod

        assert payment_method_label(PaymentMethod.cash, "uz") == "Naqd"

    def test_an_unknown_code_shows_the_code_rather_than_an_empty_status(self):
        # Blank would read as "this payment has no status", which is worse than
        # an untranslated word.
        assert payment_status_label("teleported", "uz") == "teleported"

    def test_a_locale_we_do_not_have_falls_back_rather_than_raising(self):
        assert normalize_locale("de") == "uz"
        assert normalize_locale("ru-RU") == "ru"
        assert normalize_locale(None) == "uz"
        assert payment_method_label("cash", "de") == "Naqd"


# ── filenames ───────────────────────────────────────────────────────────────

class TestDocumentFilenames:
    def test_the_contract_number_is_the_filename(self):
        assert document_filename(
            "contract", reference="MDD-2026-000001", locale="uz",
        ) == "MDD-2026-000001-shartnoma.pdf"

    def test_a_receipt_is_named_for_its_contract_too(self):
        assert document_filename(
            "payment_receipt", reference="MDD-2026-000001", locale="uz",
        ) == "MDD-2026-000001-chek.pdf"

    @pytest.mark.parametrize("hostile", [
        "../../etc/passwd", "..\\..\\windows", "a/b/c", "x\x00y", "MDD 2026\n000001",
    ])
    def test_a_filename_can_never_carry_a_path(self, hostile):
        name = document_filename("contract", reference=hostile, locale="uz")
        for bad in ("/", "\\", "..", "\x00", "\n"):
            assert bad not in name, f"{bad!r} survived in {name!r}"

    def test_a_cyrillic_or_uzbek_reference_still_produces_a_usable_name(self):
        name = document_filename("contract", reference="Шартнома-№5", locale="ru")
        assert name.endswith("-dogovor.pdf")
        assert name.isascii()

    def test_an_empty_reference_still_produces_a_file_somebody_can_save(self):
        assert document_filename("contract", reference=None, locale="uz") == "muddato-shartnoma.pdf"

    def test_the_name_is_bounded_so_a_client_cannot_reject_it(self):
        assert len(document_filename("contract", reference="A" * 500, locale="uz")) < 80

    def test_no_identity_field_can_reach_a_filename(self):
        # The contract number is the ONLY thing that identifies the file. A
        # passport or phone in a name outlives every access control the
        # document itself has: it shows in a chat list and in a downloads
        # folder long after the file is closed.
        name = document_filename("contract", reference="MDD-2026-000001", locale="uz")
        for leak in ("AA1234567", "30112900010001", "998901112233", "901112233"):
            assert leak not in name

    def test_safe_filename_part_keeps_only_what_a_filesystem_likes(self):
        assert safe_filename_part("MDD-2026-000001") == "MDD-2026-000001"
        assert safe_filename_part("") == ""
        assert safe_filename_part(None) == ""
