"""#749 — broadcast template variables are HTML-escaped.

The audit (#749) asked: "find all message templates, verify variables are
escaped." This app has no email — all notifications go via Telegram (parse_mode
=HTML). Every personalized message routes through ``notifications.render``, which
escapes (#101) — except the two broadcast paths, which interpolated the
tenant/HQ-authored ``title``/``body`` raw into ``<b>{title}</b>\\n\\n{body}``.

These plain-text fields can contain "<" or "&" (e.g. "Sale < 50% & more"), which
makes Telegram reject the whole parse_mode=HTML message and silently fail the
broadcast for every recipient — besides being a minor injection vector. The fix
routes both paths through ``_compose_broadcast_text``, which escapes the values.
"""
from __future__ import annotations

import inspect


def test_broadcast_text_escapes_html_significant_chars() -> None:
    from app.services.notifications import _compose_broadcast_text

    out = _compose_broadcast_text("Sale < 50% & more", "Buy 1 & save")
    assert out == "<b>Sale &lt; 50% &amp; more</b>\n\nBuy 1 &amp; save"
    # The structural <b> wrapper is real HTML; the values are neutralized.
    assert out.startswith("<b>Sale &lt;")
    assert "< 50%" not in out  # the raw "<" must not survive


def test_broadcast_text_neutralizes_injected_markup() -> None:
    from app.services.notifications import _compose_broadcast_text

    out = _compose_broadcast_text("<b>x</b>", "<a href='x'>link</a> & <i>more</i>")
    # No tenant-supplied tag survives as live markup — all angle brackets escaped.
    assert "<a " not in out
    assert "<i>" not in out
    assert "&lt;a href" in out
    assert "&lt;i&gt;more&lt;/i&gt;" in out


def test_broadcast_text_handles_empty_body() -> None:
    from app.services.notifications import _compose_broadcast_text

    assert _compose_broadcast_text("Title", "") == "<b>Title</b>\n\n"


def test_both_broadcast_paths_route_through_escaper() -> None:
    """Regression guard: neither delivery path may build the message with a raw
    f-string again — both must go through the escaping helper (#749)."""
    import app.services.notifications as n

    for fn in (n.deliver_broadcast_bg, n.deliver_platform_broadcast_bg):
        src = inspect.getsource(fn)
        assert "_compose_broadcast_text(" in src, f"{fn.__name__} bypasses the escaper"
        assert 'f"<b>{b.title}' not in src, f"{fn.__name__} still interpolates title raw"
