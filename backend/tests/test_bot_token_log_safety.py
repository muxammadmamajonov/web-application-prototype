"""#742 — the Telegram bot token must never leak into logs.

The tenant-bot endpoints call ``https://api.telegram.org/bot{token}/...``. An
``httpx.HTTPStatusError`` on those calls stringifies WITH the full URL — token
included — so any error log in these handlers must record ``type(e).__name__``,
never ``str(e)`` / the raw exception. (Confirmed: HTTPStatusError.__str__ yields
``Client error '400 ...' for url 'https://api.telegram.org/bot<TOKEN>/...'``.)
"""
from __future__ import annotations

import inspect


def test_tenant_bot_endpoints_log_exception_type_not_value() -> None:
    import app.api.tenant_settings as ts

    for fn in (ts.activate_tenant_bot, ts.deactivate_tenant_bot):
        src = inspect.getsource(fn)
        # Strip comments so a doc-comment mentioning the pattern can't trip the
        # guard — we only care about executable code. (No '#' appears inside the
        # string literals in these handlers, so a plain split is safe here.)
        code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
        # Precondition: this handler really does embed the bot token in a URL.
        assert "api.telegram.org/bot{" in code, f"{fn.__name__}: precondition stale"
        # No raw-exception interpolation in a log call (would leak the token URL).
        assert '", e)' not in code, f"{fn.__name__} logs a raw exception (#742)"
        assert "str(e)" not in code, f"{fn.__name__} logs str(e) (#742)"
        # The safe pattern is used.
        assert "type(e).__name__" in code, f"{fn.__name__} must log type(e).__name__"


def test_httpx_status_error_str_embeds_url() -> None:
    """Documents WHY str(e) is unsafe here: the URL (with the token) is in the
    message. If httpx ever stops doing this, the guard above can relax."""
    import httpx

    req = httpx.Request("POST", "https://api.telegram.org/bot42:SECRET/setMyCommands")
    try:
        httpx.Response(400, request=req).raise_for_status()
    except httpx.HTTPStatusError as e:
        assert "SECRET" in str(e)  # the token leaks through str() — never log it
