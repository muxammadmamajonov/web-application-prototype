"""A tenant-supplied contract template must not be able to reach the process.

THE VULNERABILITY THIS PINS (audit, 2026-08-09, proven by execution). Custom
contract HTML — writable by any owner/manager on a plan with
`allow_pdf_template_upload` — was compiled with a full-power `jinja2.Environment`,
guarded only by the regex blocklist in `validate_custom_template`.

That blocklist loses. Its dunder rule is `__\\w+__`, which only matches contiguous
tokens, so splitting the name with Jinja's `~` concatenation walks straight
through it and out to `os`:

    {{ (cycler|attr('__ini'~'t__')|attr('__glob'~'als__'))['o'~'s'] }}

The payloads below were confirmed to render `posix`, `str.__mro__` and
`<function popen>` against the pre-fix environment. That is arbitrary code
execution in the API process: `settings.secret_key` (forge any session), the
database URL, and the key decrypting every tenant's payment credentials — running
as the `postgres` role, which bypasses RLS. One tenant could read and rewrite
every other tenant's ledger.

The fix is not a better blocklist — the attacker picks the expression and Jinja's
object graph has many routes to the same place. It is `SandboxedEnvironment`,
which mediates attribute access at the engine. These tests therefore assert the
SANDBOX holds, deliberately feeding payloads the regex validator accepts.
"""
from __future__ import annotations

import pytest

from app.services.pdf import _untrusted_env, validate_custom_template

# Each of these is accepted by the regex validator and executed under a plain
# Environment. They are the actual proof-of-concept payloads.
ESCAPES = [
    pytest.param(
        "{{ (cycler|attr('__ini'~'t__')|attr('__glob'~'als__'))['o'~'s']|attr('nam'~'e') }}",
        id="os.name via concat-split dunder",
    ),
    pytest.param(
        "{{ ''|attr('__cla'~'ss__')|attr('__mr'~'o__') }}",
        id="class traversal to object subclasses",
    ),
    pytest.param(
        "{{ (cycler|attr('__ini'~'t__')|attr('__glob'~'als__'))['o'~'s']|attr('pop'~'en') }}",
        id="reach os.popen",
    ),
]


@pytest.mark.parametrize("payload", ESCAPES)
def test_sandbox_blocks_what_the_blocklist_lets_through(payload: str) -> None:
    """The regex accepts these; the sandbox must still refuse to render them."""
    # Documents the bypass rather than asserting the blocklist works — if the
    # validator is ever hardened this still passes, because the sandbox is what
    # is actually being tested.
    with pytest.raises(Exception) as exc:
        _untrusted_env.from_string(payload).render()
    rendered_anyway = str(exc.value)
    assert "posix" not in rendered_anyway
    assert "popen" not in rendered_anyway


@pytest.mark.parametrize("payload", ESCAPES)
def test_the_bypass_is_real_so_the_sandbox_is_load_bearing(payload: str) -> None:
    """Guards the guard: if the validator ever starts catching these, this test
    fails loudly and the comment above should be revisited — but until then it
    documents WHY the sandbox cannot be removed in favour of the regex."""
    validate_custom_template(payload)  # must NOT raise


def test_a_legitimate_template_still_renders() -> None:
    """The sandbox must not cost tenants their actual templating."""
    tpl = _untrusted_env.from_string(
        "{{ tenant.name }} — {{ installment.contract_no }} — {{ totals.amount }}"
    )
    out = tpl.render(
        tenant={"name": "Demo Electro"},
        installment={"contract_no": "MDD-2026-0001"},
        totals={"amount": "1 000 000"},
    )
    assert out == "Demo Electro — MDD-2026-0001 — 1 000 000"


def test_autoescape_survives_the_sandbox() -> None:
    """Customer-supplied data must still be escaped inside a custom template."""
    out = _untrusted_env.from_string("{{ customer.name }}").render(
        customer={"name": "<script>alert(1)</script>"},
    )
    assert "<script>" not in out
