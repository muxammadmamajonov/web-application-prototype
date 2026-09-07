"""The one path from a public form to the Muddato support channel.

Both public forms — the contact form and the demo request — hand their lead to a
human the same way: format it as Telegram HTML and post it into the support
channel. Extracted here so there is a single escaping rule and a single failure
behaviour rather than a copy per form.

No lead is persisted: contact-form storage (MKT-009) is LATER, and the demo
request rides the same channel rather than growing a table for six shops.

Only what the visitor submitted travels to the channel. The caller's IP address
and User-Agent are deliberately absent: a salesperson answering the lead has no
use for either, and the consent the form collects covers being *contacted*, not
having the request environment recorded.
"""
from __future__ import annotations

import html
import logging
import re

from aiogram.exceptions import TelegramAPIError
from fastapi import HTTPException, status

from app.api.bot import _bot
from app.config import settings

logger = logging.getLogger("muddato.support")

#: Per-field ceiling, counted in the characters the message actually carries —
#: i.e. *after* escaping. Bounding the raw value instead lets escaping expand it
#: afterwards: 200 ampersands pass a 200-character raw bound and then occupy
#: 1000 characters, which is how a 2000-character contact message became an
#: 11 394-character send that Telegram refuses outright, losing the lead.
FIELD_LIMIT = 200

#: Telegram's hard ceiling for one ``sendMessage`` text. A message over it is
#: rejected in full; there is no partial delivery to fall back on.
TELEGRAM_MESSAGE_LIMIT = 4096

#: Appended when the final bound has to cut. One character, and never inside a
#: tag or a character reference — see :func:`bound_for_telegram`.
TRUNCATION_MARK = "…"

_TAG = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>")

#: The bound and the closing tags it must reserve room for are mutually
#: dependent, so :func:`bound_for_telegram` iterates to a fixed point. Our
#: templates nest one level, which settles in two passes; the cap only exists so
#: a pathological future template cannot spin.
_MAX_BOUND_PASSES = 8


def _cut(markup: str, limit: int) -> str:
    """Longest prefix of `markup` within `limit` that ends outside a tag and
    outside a character reference.

    Ending inside either is not a cosmetic problem: Telegram parses the whole
    message with ``parse_mode="HTML"`` and rejects a malformed one, so a cut
    through ``&amp;`` or through ``<b>`` drops the lead just as surely as the
    length did. Every entity ``html.escape`` emits (``&amp;`` ``&lt;`` ``&gt;``)
    opens with ``&`` and closes with ``;`` and contains no inner ``&``, so a
    trailing ``&`` with no ``;`` after it is exactly the incomplete case.
    """
    if len(markup) <= limit:
        return markup
    cut = markup[:limit]
    if cut.rfind("<") > cut.rfind(">"):
        cut = cut[: cut.rfind("<")]
    amp = cut.rfind("&")
    if amp != -1 and ";" not in cut[amp:]:
        cut = cut[:amp]
    return cut


def esc(value: object, *, limit: int = FIELD_LIMIT) -> str:
    """Make a field safe for ``parse_mode="HTML"``, then bound what that costs.

    ``html.escape`` covers ``&`` as well as ``<`` and ``>``. A bare ``&`` —
    plausible in a real business name like "Ali & Vali" — is a parse error to
    Telegram, which would fail the send and drop the lead.

    Escaping happens *before* the bound so `limit` means what it says. The
    reverse order is what let a bounded field expand fivefold on the way out.
    """
    text = ("" if value is None else str(value)).strip()
    return _cut(html.escape(text, quote=False), limit) or "—"


def _unclosed(markup: str) -> list[str]:
    """Tags still open at the end of `markup`, outermost first."""
    stack: list[str] = []
    for m in _TAG.finditer(markup):
        name = m.group(1).lower()
        if not m.group(0).startswith("</"):
            stack.append(name)
        elif name in stack:
            while stack.pop() != name:
                pass
    return stack


def bound_for_telegram(text: str, *, limit: int = TELEGRAM_MESSAGE_LIMIT) -> str:
    """Guarantee a formatted message Telegram will accept.

    Per-field bounds already keep both templates far under the ceiling, so this
    is the invariant rather than the everyday path: it holds even if a template
    grows a field or a limit is raised, and it fails towards a delivered-but-
    shortened lead rather than a refused send.

    The cut lands outside any tag and any character reference, and whatever tags
    it leaves open are closed, so the result is always parseable.
    """
    if len(text) <= limit:
        return text
    reserve = len(TRUNCATION_MARK)
    for _ in range(_MAX_BOUND_PASSES):
        body = _cut(text, limit - reserve)
        closing = "".join(f"</{t}>" for t in reversed(_unclosed(body)))
        needed = len(TRUNCATION_MARK) + len(closing)
        if needed <= reserve:
            return body + TRUNCATION_MARK + closing
        reserve = needed
    # Unreachable for any template this module formats. Dropping the markup
    # entirely is the one answer that cannot be malformed.
    return _cut(_TAG.sub("", text), limit - len(TRUNCATION_MARK)) + TRUNCATION_MARK


async def send_to_support(text: str) -> None:
    """Post a formatted lead into the support channel.

    Raises ``502`` on transport failure so the caller sees an explicit failure and
    can retry, rather than a silent success over a lead that never arrived. The
    log line carries the transport error only — never the lead's contents, and
    never anything about the caller.
    """
    try:
        # (MUDD-71 L1) Read at call time, not import time: the channel is
        # configuration (SUPPORT_CHANNEL_ID), and binding it at import would
        # freeze whatever the environment happened to be when this module was
        # first touched — and make it unoverridable in a test.
        await _bot.send_message(
            chat_id=settings.support_channel_id,
            text=bound_for_telegram(text),
            parse_mode="HTML",
        )
    except TelegramAPIError as e:
        logger.error("support.send failed: %s", e)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "telegram_send_failed") from e


__all__ = (
    "FIELD_LIMIT",
    "TELEGRAM_MESSAGE_LIMIT",
    "TRUNCATION_MARK",
    "bound_for_telegram",
    "esc",
    "send_to_support",
)
