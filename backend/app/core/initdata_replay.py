"""Single-use enforcement for Telegram Mini-App ``initData`` (MUDD-76).

``verify_init_data`` proves an initData string was signed by the bot token and
that its ``auth_date`` is inside the freshness window. It does **not** prove the
string is being presented for the first time — the same signed blob stays valid
for the whole window, so anyone who captures it (shared device, a log line, a
referrer leaking out of a tenant Mini-App) can log in as the victim until it
expires. That is a bearer-equivalent replay window.

This module closes it: the first presentation *claims* the credential, every
later presentation of the same string is refused.

Design notes
------------
* **Atomic.** ``SET key … NX EX ttl`` is one round trip and one decision, so two
  simultaneous submissions of the same initData cannot both win (a
  check-then-act ``GET``/``SET`` pair could).
* **Fail closed.** A Redis outage raises ``ReplayStoreUnavailable`` and the
  caller answers 503 — matching ``auth_oidc``'s ``auth_store_unavailable``.
  Falling back to "allow" would silently restore the exact window this exists to
  remove, and an attacker who can degrade Redis would get it back on demand.
* **Purpose-scoped keys.** The invitation flow legitimately presents ONE
  initData to ``preview`` and then to ``claim``; a single global key space would
  make the second call look like an attack. Callers pass the purpose they are
  spending the credential for, and only the login purpose is wired up today.
* **The stored key is a digest, not the credential.** Within its window the
  initData ``hash`` still authenticates its bearer, so it is never written to
  Redis (or a log) verbatim.
"""
from __future__ import annotations

import hashlib
import logging

from app.core.redis import get_redis

log = logging.getLogger("muddato.initdata_replay")

#: Namespace prefix for the seen-set. Distinct from ``rl:*`` so the test-suite's
#: rate-limit flush cannot wipe live replay state.
_KEY_PREFIX = "tg:initdata:used"


class ReplayStoreUnavailable(RuntimeError):
    """The seen-store could not be reached, so single-use cannot be enforced."""


def _key(*, purpose: str, init_data_hash: str) -> str:
    digest = hashlib.sha256(init_data_hash.encode()).hexdigest()
    return f"{_KEY_PREFIX}:{purpose}:{digest}"


async def claim_initdata(
    init_data_hash: str,
    *,
    purpose: str,
    ttl_seconds: int,
) -> bool:
    """Spend one initData for ``purpose``.

    Returns ``True`` on the first presentation and ``False`` for every replay
    inside the TTL. Raises ``ReplayStoreUnavailable`` if the store is unreachable
    — callers MUST treat that as a refusal, never as a pass.

    ``ttl_seconds`` must cover the remaining acceptance window, otherwise the key
    expires while the initData is still verifiable and replay reopens. Callers
    pass the full ``max_age_seconds``, which is always ≥ the remainder.
    """
    key = _key(purpose=purpose, init_data_hash=init_data_hash)
    try:
        # nx=True → the write happens only if the key is absent, and the reply
        # tells us which of the two it was. redis-py returns True or None.
        first_use = await get_redis().set(key, "1", ex=max(ttl_seconds, 1), nx=True)
    except Exception as e:
        # No hash, no key, no user id in the log line: this fires on an
        # infrastructure fault, and the identifiers involved are live credentials.
        log.warning("initdata_replay.store_unavailable purpose=%s err=%s", purpose, type(e).__name__)
        raise ReplayStoreUnavailable(purpose) from e
    return bool(first_use)
