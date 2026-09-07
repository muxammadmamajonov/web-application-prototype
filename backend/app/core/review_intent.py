"""Opaque, signed pointers for Telegram `startapp` deep links.

INVARIANT: a `startapp` parameter is public — it rides in a group message and
can be read, forwarded and replayed. It therefore names a destination and
proves nothing.

INVARIANT: resolving an intent yields a destination only. Authorization is
evaluated separately, against the session, as if the intent did not exist.

INVARIANT: signed with domain separation over SECRET_KEY, compared in constant
time, and bounded to Telegram's 64-character `[A-Za-z0-9_-]` limit.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time
from uuid import UUID

from app.config import settings

#: Telegram's hard ceiling on a `startapp` parameter.
MAX_STARTAPP_LEN = 64

#: How long a link in a chat stays useful. Long enough that a shop owner who
#: reads the notification the next morning still lands on the right screen;
#: short enough that a message forwarded weeks later opens nothing in
#: particular. Expiry is a tidiness property, not a security one — the
#: authorization check behind it does not care how old the link is.
INTENT_TTL_SECONDS = 7 * 24 * 3600

#: Purposes an intent may name. A closed set, so a caller cannot invent a
#: destination and a signature minted for one screen cannot open another.
PURPOSE_PAYMENT_REVIEW = "pr"
_PURPOSES = {PURPOSE_PAYMENT_REVIEW}

#: Domain separation. The same `SECRET_KEY` signs sessions, download URLs and
#: receipt capabilities; without a distinct prefix a signature minted for one
#: could be replayed as another.
_DOMAIN = b"muddato.review-intent.v1"

#: 10 bytes of tag. A forgery has to be produced online against an endpoint
#: that rate-limits and that checks authorization afterwards anyway, so the
#: budget is spent on staying inside 64 characters instead.
_TAG_BYTES = 10


class InvalidIntent(ValueError):
    """The intent is malformed, expired, or not signed by us. Deliberately ONE
    exception for all three: telling a prober which part of a guess was wrong
    is free help, and no legitimate caller can act on the difference."""


def _sign(payload: bytes) -> bytes:
    key = (settings.secret_key or "").encode()
    return hmac.new(key, _DOMAIN + payload, hashlib.sha256).digest()[:_TAG_BYTES]


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def mint(purpose: str, object_id: UUID, *, now: int | None = None) -> str:
    """An opaque intent naming one screen and one row.

    The payload is the 16 raw bytes of the UUID plus a 4-byte expiry — not the
    36-character text form, which would not leave room for a signature inside
    Telegram's limit.
    """
    if purpose not in _PURPOSES:
        raise ValueError(f"unknown_intent_purpose: {purpose}")
    exp = int(now if now is not None else time.time()) + INTENT_TTL_SECONDS
    payload = purpose.encode() + object_id.bytes + struct.pack(">I", exp)
    token = _b64(payload + _sign(payload))
    if len(token) > MAX_STARTAPP_LEN:      # pragma: no cover — arithmetic is fixed
        raise ValueError("intent_too_long_for_startapp")
    return token


def resolve(token: str, *, now: int | None = None) -> tuple[str, UUID]:
    """``(purpose, object_id)`` for a valid intent, else :class:`InvalidIntent`.

    Resolving tells the caller WHERE to look. It says nothing whatsoever about
    whether the person holding it may see what is there — that check happens
    afterwards, against the session, every time.
    """
    if not token or len(token) > MAX_STARTAPP_LEN:
        raise InvalidIntent("bad_intent")
    try:
        raw = _unb64(token)
    except Exception:
        raise InvalidIntent("bad_intent") from None

    # 2 purpose + 16 uuid + 4 expiry + tag
    if len(raw) != 2 + 16 + 4 + _TAG_BYTES:
        raise InvalidIntent("bad_intent")
    payload, tag = raw[:-_TAG_BYTES], raw[-_TAG_BYTES:]

    # Constant-time: a timing-distinguishable compare leaks the tag one byte at
    # a time to anyone willing to measure.
    if not hmac.compare_digest(tag, _sign(payload)):
        raise InvalidIntent("bad_intent")

    purpose = payload[:2].decode(errors="replace")
    if purpose not in _PURPOSES:
        raise InvalidIntent("bad_intent")
    exp = struct.unpack(">I", payload[18:22])[0]
    if int(now if now is not None else time.time()) >= exp:
        raise InvalidIntent("bad_intent")
    return purpose, UUID(bytes=payload[2:18])


def payment_review_link(payment_id: UUID) -> str:
    """The `t.me` deep link a group/topic button points at.

    Used where a `web_app` button is not available — Telegram only offers those
    in private chats, and the tenant notification target may be a channel or a
    forum topic. The Main Mini App deep link works in all of them.
    """
    from app.services.notifications import miniapp_deeplink

    return miniapp_deeplink(mint(PURPOSE_PAYMENT_REVIEW, payment_id))
