"""The reminder text a customer actually receives (MUDD-111).

The owner reported this message reaching a real customer:

    Salom, Muxammad! {days_left} kun ichida 4800000.00 UZS to'lash kerak.
    Shartnoma: {contract}

Every assertion here exists because one of those three defects — an unresolved
placeholder, an unformatted amount, a hardcoded currency — got all the way to a
paying customer while the product's own preview screen showed perfect copy.

The load-bearing test is `test_no_default_template_leaves_a_placeholder`: it
renders each shipped default against the context the SEND PATH builds, not a
context written for the test. A test with its own dict would have passed
happily on the broken code, which is exactly how this survived.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.notifications import (
    _LEGACY_ALIASES,
    format_money,
    reminder_context,
    render,
)
from app.services.scheduler import _DEFAULTS_UZ


def send_path_context() -> dict[str, str]:
    """Exactly the call `send_reminders_job` makes, with realistic values.

    Kept as a helper rather than a fixture so the argument list is visible: if
    someone adds a parameter to `reminder_context`, this file stops compiling
    instead of silently testing a stale shape.
    """
    return reminder_context(
        customer_name="Muxammad",
        currency="USD",
        amount=Decimal("4800000.00"),
        due_date="2026-09-15",
        contract_number="MDD-2026-000123",
        remaining=Decimal("14400000.00"),
        days_overdue=0,
        days_until_due=3,
        phone="+998901234567",
    )


@pytest.mark.parametrize("key", sorted(_DEFAULTS_UZ))
def test_no_default_template_leaves_a_placeholder(key: str) -> None:
    """The regression that names the bug: nothing shipped may render a `{`."""
    text = render(_DEFAULTS_UZ[key], **send_path_context())
    assert "{" not in text and "}" not in text, (
        f"template {key!r} rendered with an unresolved placeholder: {text!r}"
    )


@pytest.mark.parametrize("key", sorted(_DEFAULTS_UZ))
def test_no_default_template_hardcodes_a_currency(key: str) -> None:
    """A shop trading in USD must not dun its customers in UZS.

    The removed seed wrote a literal "UZS" into every template. The check is on
    the TEMPLATE, not the rendered text, because a rendered USD reminder that
    also said "UZS" would still contain the correct token and pass a naive
    substring assertion.
    """
    tpl = _DEFAULTS_UZ[key]
    assert "UZS" not in tpl, f"template {key!r} hardcodes a currency: {tpl!r}"
    if "{amount}" in tpl or "{remaining}" in tpl:
        assert "{currency}" in tpl, (
            f"template {key!r} shows money with no currency placeholder"
        )


def test_legacy_placeholder_names_still_resolve() -> None:
    """`{days_left}` and `{contract}` are what the Settings UI advertises and
    what pre-fix tenant rows contain. They must resolve, not be dropped."""
    ctx = send_path_context()
    for alias, canonical in _LEGACY_ALIASES.items():
        assert ctx[alias] == ctx[canonical]
    text = render("{days_left} kun · {contract}", **ctx)
    assert text == "3 kun · MDD-2026-000123"


def test_preview_and_send_cannot_disagree() -> None:
    """The defect's real shape: the preview endpoint supplied a superset of the
    cron's variables, so a broken template looked fine in "Test yuborish".

    Comparing the key sets of two calls to `reminder_context` is a tautology —
    it can never fail while both call sites route through the same function,
    no matter what the CALLER around them does. So this checks the SOURCE of
    the preview endpoint itself first, the same source-reading technique
    `test_the_seed_has_no_private_copy_of_the_defaults` (above) uses: it must
    actually CALL `reminder_context(...)`, not reimplement its own dict — a
    rival dict there is exactly the MUDD-111 regression this file exists to
    catch, reintroduced.
    """
    import inspect

    from app.api.tenant_settings import test_reminder_send

    src = inspect.getsource(test_reminder_send)
    # Comment lines may freely mention the old rival dict to explain the bug;
    # only executable lines are checked.
    code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    assert "reminder_context(" in code, (
        "the preview endpoint no longer calls reminder_context(...) directly — "
        "it may have grown a rival context dict, which is the exact defect "
        "this guard exists to catch"
    )

    # Belt and braces: the two call sites' key sets must still agree, in case
    # `reminder_context` itself grows a parameter one caller forgets to pass.
    send_keys = set(send_path_context())
    preview_keys = set(reminder_context(
        customer_name="Test mijoz",
        currency="UZS",
        amount=850_000,
        due_date="2026-06-03",
        contract_number="MDD-2026-000123",
        remaining=2_550_000,
        days_overdue=2,
        days_until_due=3,
        phone="+998901234567",
    ))
    assert send_keys == preview_keys


@pytest.mark.parametrize(("raw", "expected"), [
    (Decimal("4800000.00"), "4 800 000"),   # the exact figure from the report
    (4_800_000,             "4 800 000"),
    ("850000",              "850 000"),
    (0,                     "0"),
    (None,                  "0"),
    (Decimal("2550000.75"), "2 550 000"),   # truncated, matching the frontend
    (Decimal("999"),        "999"),
])
def test_format_money_matches_the_ui(raw: object, expected: str) -> None:
    assert format_money(raw) == expected


def test_format_money_echoes_what_it_cannot_parse() -> None:
    """Better a visibly odd string than a confident "0" for a real debt."""
    assert format_money("n/a") == "n/a"


def test_rendered_amount_is_never_a_raw_decimal() -> None:
    """The customer-visible symptom, asserted end to end."""
    text = render(_DEFAULTS_UZ["before_due"], **send_path_context())
    assert "4800000.00" not in text
    assert "4 800 000" in text
    assert "USD" in text and "UZS" not in text


# ── structural guards ──────────────────────────────────────────────────────
#
# These two came from the parallel Backend Engineer lane on this issue
# (branch `fix/mudd111-notification-reminders`, commit e939b2ac) — the rest of
# that branch is superseded by this one, but these guards are its own finding
# and are kept rather than discarded. See the PR body for the full comparison.

def test_the_seed_has_no_private_copy_of_the_defaults() -> None:
    """The seed must CALL the canonical defaults, never restate them.

    Checked on the source text rather than the value, because the failure being
    prevented is a *second copy* appearing — which a value-equality assertion
    would happily pass on the day it is introduced and only fail years later
    when the two copies drift.
    """
    import inspect

    from app.api.tenant_settings import _get_or_create_settings

    src = inspect.getsource(_get_or_create_settings)
    # Comment lines necessarily quote the old broken placeholders to explain the
    # bug; only executable lines are checked.
    code = "\n".join(
        ln for ln in src.splitlines() if not ln.strip().startswith("#")
    )
    assert "default_reminder_templates()" in code
    for stale in ("{days_left}", "{contract}", "{phone}", ' UZS"'):
        assert stale not in code, f"stale seed literal {stale!r} is back"


def test_deeplink_import_is_not_gated_on_the_customer_branch() -> None:
    """`installment_pay_deeplink` must be bound unconditionally.

    It used to be imported inside ``if recipient is not None:`` — the
    customer-has-a-linked-Telegram-user branch — while the OWNER's overdue
    button, a wholly separate branch, also needs it. An overdue contract whose
    customer has no linked user would raise ``NameError``; nothing wraps the
    per-installment loop, so that one row would abort the cron and silently
    drop every remaining reminder that day.
    """
    import inspect

    from app.services.scheduler import send_reminders_job

    top_import = inspect.getsource(send_reminders_job).split(
        "async with worker_session", 1
    )[0]
    assert "installment_pay_deeplink" in top_import
    assert "tenant_installment_deeplink" in top_import
